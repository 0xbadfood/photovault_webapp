import requests
import time

BASE_URL = "http://localhost:8877"
USERID = "testuser"
PASSWORD = "passw0rd"

def run():
    print("1. Checking index...")
    try:
        r = requests.get(BASE_URL)
        print(f"Index status: {r.status_code}")
    except Exception as e:
        print(f"Index failed: {e}")
        return

    print("2. Logging in...")
    r = requests.post(f"{BASE_URL}/api/login", json={"userid": USERID, "password": PASSWORD})
    print(f"Login response: {r.text}")
    if r.status_code != 200:
        return

    print("3. Scanning files...")
    r = requests.post(f"{BASE_URL}/api/scan", json={"userid": USERID})
    print(f"Scan response: {r.text}")

    print("4. Generating thumbnails...")
    start = time.time()
    r = requests.post(f"{BASE_URL}/api/thumbnails/generate", json={"userid": USERID})
    print(f"Generate response: {r.text}")
    print(f"Generation took {time.time() - start:.2f}s")

if __name__ == "__main__":
    run()
