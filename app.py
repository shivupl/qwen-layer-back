from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import os
import uuid
import boto3
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


@app.route('/api/runpod', methods=['POST'])
def call_runpod():
    data = request.get_json() or {}
    
    # endpoint_id = os.getenv('RUNPOD_ENDPOINT_ID')
    # runpod_api_key = os.getenv('RUNPOD_API_KEY')
    # endpoint_id ="azgbx61zc364v9"
    endpoint_id ="7xyf35jciyeib6"
    runpod_api_key ="REDACTED"
    input_data = data.get('input', {})
    
    if not endpoint_id:
        return jsonify({'error': 'RunPod endpoint_id is required'}), 400
    
    if not runpod_api_key:
        return jsonify({'error': 'RunPod API key is required'}), 400
    
    runpod_url = f'https://api.runpod.ai/v2/{endpoint_id}/run'
    
    # request 
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {runpod_api_key}',
    }
    data = {
        'input': input_data
    }

    try:
        response = requests.post(
            runpod_url,
            headers=headers,
            json=data,
            timeout=300  # 5 minute timeout
        )
        response.raise_for_status()
        return jsonify(response.json()), response.status_code
    except requests.exceptions.RequestException as e:
        return jsonify({
            'error': 'Failed to call RunPod API',
            'message': str(e)
        }), 500



if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
