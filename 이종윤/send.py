import requests
import os
from dotenv import load_dotenv
load_dotenv()

API_KEY=os.getenv('API_KEY')
url = 'http://34.97.18.15:8080/send'
headers = {
    'Content-Type': 'application/json',
    'X-API-Key': API_KEY
}
data = {
    'first_blood_problem': 'SQLI',
    'first_blood_person': '정지윤',
    'first_blood_school': ""
}

response = requests.post(url, headers=headers, json=data)
print(response.json())
