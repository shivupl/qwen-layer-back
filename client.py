import requests
import json
import os

# Flask backend URL
BACKEND_URL = 'http://localhost:8000/api/runpod'

def call_runpod(input_data):
    try:
        response = requests.post(
            BACKEND_URL,
            json={'input': input_data},
            headers={'Content-Type': 'application/json'},
            timeout=300
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        # Try to get error message from response
        try:
            error_data = response.json()
            print(f"Error: {error_data.get('error', 'Unknown error')}")
        except:
            print(f"Error calling API: {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error calling API: {e}")
        return None


def r2_url(image_path):
    presign_res = requests.post(
        "http://localhost:8000/api/r2/presign-upload",
        json={"contentType": "image/png"},
        timeout=30
    )
    presign_res.raise_for_status()
    presign = presign_res.json()

    put_url = presign["putUrl"]
    get_url = presign["getUrl"]

    with open(image_path, "rb") as f:
        upload_res = requests.put(
            put_url,
            data=f,
            # headers={"Content-Type": "image/png"},
            timeout=60
        )
        upload_res.raise_for_status()

    return get_url




if __name__ == '__main__':
    test_image = "put_test.png"  # any local image
    print("Requesting presigned R2 upload URL...")
    url = r2_url(test_image)

    print("Upload successful!")
    print("Accessible at:")
    print(url)


    input_data = {
        'image_url': url,
        'layers': 3,
        'resolution': 640,
        'steps': 40,
        # 'seed': 123
    }
    
    print("Calling RunPod API...")
    result = call_runpod(input_data)
    
    if result:
        print("Response:")
        print(json.dumps(result, indent=2))
    else:
        print("Failed to get response")




