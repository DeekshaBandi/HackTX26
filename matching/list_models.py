"""
Prints the Gemma models your API key can use.
Run:  python list_models.py
"""
import os

from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

for m in client.models.list():
    if "gemma" in m.name.lower():
        print(m.name.replace("models/", ""))