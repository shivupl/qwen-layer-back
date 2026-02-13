from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import os
import uuid
import boto3
import time
from botocore.client import Config
from dotenv import load_dotenv
from typing import Optional
from openai import OpenAI

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError



load_dotenv()


db = SQLAlchemy()

app = Flask(__name__)
CORS(app) 

# DB Config
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
db.init_app(app)

# Costs for the credits system
COSTS = {"640p": 1, "1080p": 2}



s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["R2_ENDPOINT"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    region_name="auto",
    config=Config(signature_version="s3v4"),
)




# DB Stuff
def require_json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "Invalid JSON"}), 400)
    return data, None

def ensure_user_row(express_user_id: str):
    """
    Ensure `users` and `credit_balance` rows exist.
    Safe to call every time.
    """
    db.session.execute(text("""
        insert into users (express_user_id)
        values (:uid)
        on conflict (express_user_id) do nothing
    """), {"uid": express_user_id})

    db.session.execute(text("""
        insert into credit_balance (express_user_id, balance)
        values (:uid, 0)
        on conflict (express_user_id) do nothing
    """), {"uid": express_user_id})


# OpenAI client 
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set in .env")

CAPTION_SYSTEM_PROMPT = """
You describe images for downstream image-editing models.

Rules:
- Describe only what is clearly visible.
- Do not infer identities, story, or character names.
- Be concise and factual.
- Quote text exactly. If unsure, write "Unclear".
- Provide approximate location for each text element (top-left, top-center, top-right, center-left, center, center-right, bottom-left, bottom-center, bottom-right).

Output format (use exactly this structure):

Scene:
- <...>

Main subject:
- <...>

Background:
- <...>

Objects & details:
- <...>

Text in image (with location):
- "<text>" — <location>

Style:
- <...>

Composition:
- <...>
""".strip()



@app.post("/api/r2/presign-upload")
def presign_upload():
    data = request.json
    content_type = data.get("contentType", "application/octet-stream")

    # unique obj key
    key = f"inputs/{uuid.uuid4()}.png"

    put_url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": os.environ["R2_BUCKET"],
            "Key": key,
        },
        ExpiresIn=600,
    )

    # signed GET URL
    get_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": os.environ["R2_BUCKET"], "Key": key},
        ExpiresIn=3600,
    )


    return jsonify({
        "putUrl": put_url,
        "getUrl": get_url,
        "key": key
    })


@app.post("/api/r2/upload")
def upload_file():
    """Upload file to R2 via backend to avoid CORS issues"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    try:
        # Generate unique key
        key = f"inputs/{uuid.uuid4()}.png"
        
        # Upload to R2
        s3.upload_fileobj(
            file,
            os.environ["R2_BUCKET"],
            key,
            ExtraArgs={'ContentType': file.content_type or 'image/png'}
        )
        
        # Generate signed GET URL
        get_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": os.environ["R2_BUCKET"], "Key": key},
            ExpiresIn=3600,
        )
        
        return jsonify({
            "getUrl": get_url,
            "key": key
        })
    except Exception as e:
        return jsonify({
            'error': 'Failed to upload file',
            'message': str(e)
        }), 500


@app.get("/api/r2/fetch-image")
def fetch_image():
    """Fetch image from R2 URL via backend to avoid CORS issues"""
    image_url = request.args.get('url')
    
    if not image_url:
        return jsonify({'error': 'No URL provided'}), 400
    
    try:
        # Fetch the image from R2
        response = requests.get(image_url, timeout=30, stream=True)
        response.raise_for_status()
        
        # Get content type from response
        content_type = response.headers.get('Content-Type', 'image/png')
        
        # Return the image with proper headers
        from flask import Response
        return Response(
            response.content,
            mimetype=content_type,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Content-Type': content_type,
                'Content-Length': str(len(response.content))
            }
        )
    except Exception as e:
        return jsonify({
            'error': 'Failed to fetch image',
            'message': str(e)
        }), 500


@app.route('/api/runpod', methods=['POST'])
def call_runpod():
    """Start RunPod job and poll until completion"""
    data = request.get_json() or {}
    
    # endpoint_id = os.environ['RUNPOD_ENDPOINT_ID']
    # runpod_api_key = os.environ['RUNPOD_API_KEY']
    endpoint_id = (os.getenv("RUNPOD_ENDPOINT_ID") or "").strip()
    runpod_api_key = (os.getenv("RUNPOD_API_KEY") or "").strip()
    input_data = data.get('input', {})
    
    if not endpoint_id:
        return jsonify({'error': 'RunPod endpoint_id is required'}), 400
    
    if not runpod_api_key:
        return jsonify({'error': 'RunPod API key is required'}), 400
    
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {runpod_api_key}',
    }
    
    # Step 1: Start the job
    runpod_run_url = f'https://api.runpod.ai/v2/{endpoint_id}/run'
    
    try:
        # Start the job
        response = requests.post(
            runpod_run_url,
            headers=headers,
            json={'input': input_data},
            timeout=30
        )
        response.raise_for_status()
        job_data = response.json()
        job_id = job_data.get('id')
        
        if not job_id:
            return jsonify({
                'error': 'No job ID returned from RunPod',
                'response': job_data
            }), 500
        
        # Step 2: Poll for completion
        runpod_status_url = f'https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}'
        max_wait_time = 300  # 5 minutes max
        poll_interval = 2  # Check every 2 seconds
        start_time = time.time()
        
        while True:
            # Check if we've exceeded max wait time
            if time.time() - start_time > max_wait_time:
                return jsonify({
                    'error': 'Job timed out',
                    'job_id': job_id,
                    'status': 'TIMEOUT'
                }), 504
            
            # Check job status
            status_response = requests.get(
                runpod_status_url,
                headers=headers,
                timeout=30
            )
            status_response.raise_for_status()
            status_data = status_response.json()
            
            status = status_data.get('status', '').upper()
            
            if status == 'COMPLETED':
                # Job is done, return the result
                return jsonify(status_data), 200
            elif status in ['FAILED', 'CANCELLED']:
                return jsonify({
                    'error': f'Job {status.lower()}',
                    'job_id': job_id,
                    'status': status,
                    'data': status_data
                }), 500
            elif status in ['IN_QUEUE', 'IN_PROGRESS']:
                # Still processing, wait and check again
                time.sleep(poll_interval)
            else:
                # Unknown status, wait and check again
                time.sleep(poll_interval)
                
    except requests.exceptions.RequestException as e:
        return jsonify({
            'error': 'Failed to call RunPod API',
            'message': str(e)
        }), 500



# Captioning function 
def get_caption_for_image(image_url: str) -> Optional[str]:
    client = OpenAI(api_key=OPENAI_API_KEY)

    resp = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": CAPTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Generate the caption in the exact format."},
                    {"type": "input_image", "image_url": image_url},
                ],
            },
        ],
    )

    caption = (resp.output_text or "").strip()
    return caption or None

@app.route("/caption", methods=["POST"])
def caption():
    # 1) Parse JSON body safely
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body. Expected an object with image_url."}), 400

    # 2) Extract and validate image_url
    image_url = (data.get("image_url") or "").strip()
    if not image_url:
        return jsonify({"error": "Missing image_url in JSON body"}), 400

    # 3) Call OpenAI to generate caption
    try:
        caption_text = get_caption_for_image(image_url)
    except Exception as e:
        return jsonify({"error": "Caption failed", "message": str(e)}), 500

    # 4) Return result
    return jsonify({"caption": caption_text or ""}), 200


# Credit System Endpoints
@app.post("/api/credits/balance")
def credits_balance():
    data, err = require_json()
    if err: return err
    uid = (data.get("express_user_id") or "").strip()
    if not uid:
        return jsonify({"error": "Missing express_user_id"}), 400

    try:
        ensure_user_row(uid)

        row = db.session.execute(
            text("select balance from credit_balance where express_user_id=:uid"),
            {"uid": uid}
        ).fetchone()

        db.session.commit()
        balance = int(row[0]) if row else 0
        return jsonify({"balance": balance}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Failed to fetch balance", "message": str(e)}), 500

@app.post("/api/credits/consume")
def credits_consume():
    data, err = require_json()
    if err: return err

    uid = (data.get("express_user_id") or "").strip() #get the user id from express frontend
    action = (data.get("action") or "").strip() #action to consume credits for
    if action not in COSTS:
        return jsonify({"error": f"Invalid action: {action}"}), 400
    amount = COSTS[action] #get the cost of the action
    action_ref = (data.get("action_ref") or "").strip()  # optional but recommended unique id
    app_id = (data.get("app_id") or "qwen-layer-addon").strip() #app id from express frontend
    if not action_ref:
        return jsonify({"error": "Missing action_ref"}), 400


    #check if the user id is missing
    if not uid:
        return jsonify({"error": "Missing express_user_id"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be > 0"}), 500

    try:
        ensure_user_row(uid)
        # 1) Insert ledger first (idempotency gate)
        ins = db.session.execute(text("""
            insert into credit_ledger (express_user_id, delta, reason, external_ref, app_id)
            values (:uid, :delta, :reason, :ref, :app_id)
            on conflict (app_id, external_ref) do nothing
            returning id
        """), {
            "uid": uid,
            "delta": -amount,
            "reason": action,
            "ref": action_ref,
            "app_id": app_id
        }).fetchone()

        if not ins:
            # duplicate request: return current balance, don’t charge again
            bal = db.session.execute(
                text("select balance from credit_balance where express_user_id=:uid"),
                {"uid": uid}
            ).scalar_one()
            db.session.rollback()  # or just omit any commit/rollback, but rollback is safest
            return jsonify({"ok": True, "idempotent": True, "balance": int(bal)}), 200

        # 2) Now decrement (atomic)
        row = db.session.execute(text("""
            update credit_balance
            set balance = balance - :amt
            where express_user_id = :uid
            and balance >= :amt
            returning balance
        """), {"uid": uid, "amt": amount}).fetchone()

        if not row:
            # insufficient credits → rollback ledger insert too
            db.session.rollback()
            bal = db.session.execute(
                text("select balance from credit_balance where express_user_id=:uid"),
                {"uid": uid}
            ).scalar_one()
            return jsonify({"ok": False, "error": "insufficient_credits", "balance": int(bal)}), 402

        new_balance = int(row[0])
        db.session.commit()
        return jsonify({"ok": True, "balance": new_balance}), 200


    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Failed to consume credits", "message": str(e)}), 500


# Credit System - Admin/testing endpoints
def require_admin(req: request):
    key = req.headers.get("Authorization", "")
    if key.startswith("Bearer "):
        key = key[len("Bearer "):].strip()
    return key and key == os.getenv("ADMIN_API_KEY")

@app.post("/api/credits/grant")
def credits_grant():
    if not require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    data, err = require_json()
    if err:
        return err

    uid = (data.get("express_user_id") or "").strip()
    amount = int(data.get("amount") or 0)
    reason = (data.get("reason") or "admin_grant").strip()
    ref = (data.get("external_ref") or "").strip()  # optional idempotency key
    app_id = (data.get("app_id") or "billing").strip()

    if not uid:
        return jsonify({"error": "Missing express_user_id"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be > 0"}), 400

    try:
        ensure_user_row(uid)

        if ref:
            ins = db.session.execute(text("""
                insert into credit_ledger (express_user_id, delta, reason, external_ref, app_id)
                values (:uid, :delta, :reason, :ref, :app_id)
                on conflict (app_id, external_ref) do nothing
                returning id
            """), {"uid": uid, "delta": amount, "reason": reason, "ref": ref, "app_id": app_id}).fetchone()

            if not ins:
                bal = db.session.execute(
                    text("select balance from credit_balance where express_user_id=:uid"),
                    {"uid": uid}
                ).scalar_one()
                db.session.rollback()
                return jsonify({"ok": True, "idempotent": True, "balance": int(bal)}), 200
        else:
            db.session.execute(text("""
                insert into credit_ledger (express_user_id, delta, reason, app_id)
                values (:uid, :delta, :reason, :app_id)
            """), {"uid": uid, "delta": amount, "reason": reason, "app_id": app_id})

        # IMPORTANT: always update balance if we got past the idempotency gate
        db.session.execute(text("""
            update credit_balance
            set balance = balance + :delta
            where express_user_id = :uid
        """), {"uid": uid, "delta": amount})

        new_bal = db.session.execute(
            text("select balance from credit_balance where express_user_id=:uid"),
            {"uid": uid}
        ).scalar_one()

        db.session.commit()
        return jsonify({"ok": True, "balance": int(new_bal)}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Failed to grant credits", "message": str(e)}), 500


@app.get("/api/credits/ledger")
def credits_ledger():
    if not require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    uid = (request.args.get("express_user_id") or "").strip()
    if not uid:
        return jsonify({"error": "Missing express_user_id"}), 400

    rows = db.session.execute(text("""
        select created_at, delta, reason, external_ref, app_id
        from credit_ledger
        where express_user_id = :uid
        order by created_at desc
        limit 100
    """), {"uid": uid}).fetchall()

    return jsonify({
        "express_user_id": uid,
        "entries": [
            {"created_at": str(r[0]), "delta": int(r[1]), "reason": r[2], "external_ref": r[3]}
            for r in rows
        ]
    }), 200




if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
