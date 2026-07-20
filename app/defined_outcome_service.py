import requests

URL = "https://www.innovatoretfs.com/define/etfs/"


def get_page():
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(URL, headers=headers, timeout=30)

    response.raise_for_status()

    return response.text