import requests
import random

def create_payment(amount, project, api_key):
    url = "https://app.pakasir.com/api/transactioncreate/qris"
    order_id = "INV" + str(random.randint(100000, 999999))
    payload = {"project": project, "order_id": order_id, "amount": amount, "api_key": api_key}
    try:
        r = requests.post(url, json=payload, timeout=15)
        data = r.json()
        if "payment" in data and "payment_number" in data["payment"]:
            return True, order_id, data["payment"]["payment_number"], None
        return False, None, None, data.get("message", str(data))
    except Exception as e:
        return False, None, None, str(e)

def cancel_payment(amount, order_id, project, api_key):
    try:
        requests.post(
            "https://app.pakasir.com/api/transactioncancel", 
            json={"project": project, "order_id": order_id, "amount": amount, "api_key": api_key}, 
            timeout=10
        )
    except: 
        pass

def check_payment_status(amount, order_id, project, api_key):
    try:
        r = requests.get(
            f"https://app.pakasir.com/api/transactiondetail?project={project}&amount={amount}&order_id={order_id}&api_key={api_key}", 
            timeout=10
        ).json()
        if "transaction" in r and r["transaction"]["status"] == "completed":
            return True
        return False
    except: 
        return False
