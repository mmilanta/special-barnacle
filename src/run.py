import uvicorn
from dotenv import load_dotenv
import os

load_dotenv()
with open("../certificate/special-barnacle-key/id_rsa", "r") as f:
    private_key = f.read()
os.environ["PRIVATE_KEY"] = private_key

from app import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)