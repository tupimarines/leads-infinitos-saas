import requests
import os
from dotenv import load_dotenv
import json
import random

load_dotenv()

BASE_URL = os.environ.get('MEGA_API_URL')
TOKEN = os.environ.get('MEGA_API_TOKEN')

headers = {
    'Authorization': TOKEN,
    'Content-Type': 'application/json'
}

def try_request(desc, method, url_suffix, params=None, json_body=None):
    print(f"\n--- Testing: {desc} ---")
    url = f"{BASE_URL}{url_suffix}"
    print(f"URL: {url}")
    if params: print(f"Params: {params}")
    if json_body is not None: print(f"Body: {json_body}")
    
    try:
        if method == 'POST':
            # If json_body is None, don't send json param
            if json_body is None:
                res = requests.post(url, params=params, headers=headers, timeout=15)
            else:
                res = requests.post(url, params=params, json=json_body, headers=headers, timeout=15)
        else:
            res = requests.get(url, params=params, headers=headers, timeout=15)
            
        print(f"Status: {res.status_code}")
        try:
             print(f"Response: {res.json()}")
        except:
             print(f"Response Text: {res.text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    # 1. List Instances (Correct Endpoint)
    try_request("List Instances (/rest/instance)", "GET", "/rest/instance")
    
    # 2. Create Instance - Payload match documentation
    # Doc shows messageData with webhook stuff
    run_id = random.randint(10000, 99999)
    name = f"debug-{run_id}"
    
    payload = {
        "messageData": {
            "webhookUrl": "",
            "webhookEnabled": True
        }
    }
    
    try_request("Create with messageData payload", "POST", "/rest/instance/init", 
                params={"instance_key": name}, json_body=payload)
    
    # 3. Create Instance - No name (since user said it's optional)
    # The doc says instance_key is required query param though? 
    # User said "não deve ser mandatório um nome". Maybe valid for manual vs API?
    # Let's try sending NO query param, but maybe with payload?
    try_request("Create without name (No Query Param)", "POST", "/rest/instance/init", 
                params={}, json_body=payload)
