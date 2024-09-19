rm -r ./venv
python3.12 -m venv venv                 
. venv/bin/activate                                                                      
pip install -r requirements.in
pip freeze > requirements.txt
