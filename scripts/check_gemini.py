# scripts/check_gemini.py
import os
from dotenv import load_dotenv
from google import genai
load_dotenv()
client = genai.Client(
 vertexai=True,
 project=os.environ["GOOGLE_CLOUD_PROJECT"],
 location=os.environ["GOOGLE_CLOUD_LOCATION"],
)
resp = client.models.generate_content(
 model=os.environ["GEMINI_MODEL"],
 contents="こんにちは、と一言で返してください。",
)
print(resp.text)