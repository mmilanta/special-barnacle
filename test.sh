curl --request PUT \
  --url 'http://127.0.0.1:5000/key_sample2' \
  --data 'Worked'

curl --request GET \
  --url 'http://127.0.0.1:5000/key_sample2' \

curl --request GET \
  --url 'http://127.0.0.1:5000/' 

curl --request DELETE \
  --url 'http://127.0.0.1:5000/key_sample'

curl --request DELETE \
  --url 'http://127.0.0.1:5000/key_sample2' \