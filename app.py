from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import os
import uuid
import boto3
import time
from botocore.client import Config
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app) 



s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["R2_ENDPOINT"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    region_name="auto",
    config=Config(signature_version="s3v4"),
)

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



if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
