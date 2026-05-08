import requests

# Replace with your proxy user credentials.
username = '0xash_DxDNa'
password = 'ulyFRWPQ~DY21J2V'

# Port `8000` rotates IPs from your proxy list.
address = 'dc.oxylabs.io:8000'

proxies = {
   'https': f'https://user-{username}:{password}@{address}'
}

response = requests.get('https://ip.oxylabs.io/location', proxies=proxies)

print(response.text)