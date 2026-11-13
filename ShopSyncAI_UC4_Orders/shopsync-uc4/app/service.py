import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from .db import get_store, run_transaction


class OrderError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def uid():
    return uuid.uuid4().hex[:16].upper()


def atomic(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        return run_transaction(lambda store: function(store, *args, **kwargs))
    return wrapped


def format_pkr(cents):
    return f"PKR {cents // 100:,}.{cents % 100:02d}"


def draft_summary(order):
    lines = [f"Draft {order['id']}",
             f"Customer: {order['customer_name']} ({order['customer_phone']})", "Items:"]
    for item in order["items"]:
        lines.append(f"- {item['quantity']} x {item['name']} ({item['sku']}) "
                     f"at {format_pkr(item['unit_price_cents'])} = "
                     f"{format_pkr(item['quantity'] * item['unit_price_cents'])}")
    lines.extend([f"Delivery address: {order['delivery_address']}",
                  f"Payment method: {order['payment_method']}",
                  f"Total: {format_pkr(order['total_cents'])}",
                  f"Reply CONFIRM {order['id']} if these details are correct."])
    return "\n".join(lines)


def phone_number(value):
    phone = re.sub(r"\D", "", str(value))
    if not 8 <= len(phone) <= 15:
        raise OrderError("Phone must have 8 to 15 digits, including country code.")
    return phone


def log(db, order_id, actor, event, **details):
    db.insert("audit_events", dict(id=uid(), order_id=order_id, actor=actor,
                                  event=event, details=details, created_at=now()))


def queue_message(db, phone, body):
    db.insert("outbox", dict(id=uid(), phone=phone, body=body, status="PENDING",
                            attempts=0, next_attempt_at=now(), created_at=now(),
                            provider_id=None, last_error=None, lease_until=None, claim_id=None))


def touch(db, order_id, **fields):
    # Writing the same order coordinates cross-collection decisions (e.g. a
    # cancellation racing a refund). MongoDB retries the losing transaction.
    fields["updated_at"] = now()
    db.update("orders", {"_id": order_id}, {"$set": fields, "$inc": {"revision": 1}})


def customer(db, phone, name=None, inbound=False):
    phone = phone_number(phone)
    row = db.get("customers", {"phone": phone})
    if row:
        if inbound:
            db.update("customers", {"_id": row["id"]}, {"$set": {"last_inbound_at": now()}})
        return row["id"]
    customer_id = uid()
    db.insert("customers", dict(id=customer_id, phone=phone, name=name or phone,
                               last_inbound_at=now() if inbound else None))
    return customer_id


def product_list():
    return get_store().all("products", sort=[("name", 1), ("_id", 1)])


@atomic
def add_product(db, sku, name, price_cents, stock_qty):
    sku = sku.strip().upper()
    if not sku or not name.strip() or price_cents < 0 or stock_qty < 0:
        raise OrderError("Provide SKU, name, nonnegative price and stock.")
    if db.get("products", {"sku": sku}):
        raise OrderError("SKU already exists.", 409)
    product = dict(id=uid(), sku=sku, name=name.strip(), price_cents=price_cents,
                   stock_qty=stock_qty, active=True)
    db.insert("products", product)
    log(db, None, "owner", "product_created", sku=sku, stock_qty=stock_qty)
    return product


@atomic
def restock_product(db, product_id, quantity):
    if quantity <= 0:
        raise OrderError("Restock quantity must be positive.")
    result = db.update("products", {"_id": product_id}, {"$inc": {"stock_qty": quantity}})
    if not result.matched_count:
        raise OrderError("Product not found.", 404)
    log(db, None, "owner", "manual_restock", product_id=product_id, quantity=quantity)
    return db.get("products", {"_id": product_id})


def order_view(db, order_id, requester_phone=None):
    order = db.get("orders", {"_id": order_id})
    if not order:
        raise OrderError("Order not found.", 404)
    person = db.get("customers", {"_id": order["customer_id"]})
    if not person or (requester_phone and person["phone"] != phone_number(requester_phone)):
        raise OrderError("Order not found.", 404)
    order["customer_phone"], order["customer_name"] = person["phone"], person["name"]
    order["risk_reasons"] = order.get("risk_reasons") or []
    for item in order["items"]:
        item["order_id"] = order_id
    order["approval"] = db.get("approvals", {"order_id": order_id})
    order["return_request"] = db.get("return_requests", {"order_id": order_id})
    return order


def get_order(order_id, requester_phone=None):
    return order_view(get_store(), order_id, requester_phone)


def list_orders():
    db = get_store()
    return [order_view(db, row["id"]) for row in
            db.all("orders", sort=[("created_at", -1), ("_id", -1)])]


def resolve_items(db, items):
    if not items or len(items) > 25 or len({i["product_id"] for i in items}) != len(items):
        raise OrderError("Provide 1 to 25 distinct products.")
    resolved = []
    for item in items:
        quantity = item["quantity"]
        if not 1 <= quantity <= 100:
            raise OrderError("Each quantity must be between 1 and 100.")
        product = db.get("products", {"_id": item["product_id"], "active": True})
        if not product:
            raise OrderError("Product not found or inactive.", 404)
        if product["stock_qty"] < quantity:
            raise OrderError(f"Insufficient stock for {product['sku']}.", 409)
        resolved.append(dict(product_id=product["id"], sku=product["sku"], name=product["name"],
                             unit_price_cents=product["price_cents"], quantity=quantity))
    return resolved, sum(i["unit_price_cents"] * i["quantity"] for i in resolved)


def validate_delivery(address, payment):
    if not address.strip() or len(address) > 500:
        raise OrderError("Provide a delivery address (maximum 500 characters).")
    if payment not in ("COD", "BANK_TRANSFER"):
        raise OrderError("Payment method must be COD or BANK_TRANSFER.")


def _draft(db, phone, name, items, delivery_address, payment_method, channel, source_key, actor):
    existing = db.get("orders", {"source_key": source_key})
    if existing:
        result = order_view(db, existing["id"])
        if result["customer_phone"] != phone_number(phone):
            raise OrderError("Source key is already used by another customer.", 409)
        return result
    validate_delivery(delivery_address, payment_method)
    resolved, total = resolve_items(db, items)
    customer_id = customer(db, phone, name)
    order_id = uid()
    db.insert("orders", dict(
        id=order_id, customer_id=customer_id, channel=channel, status="DRAFT",
        delivery_address=delivery_address.strip(), payment_method=payment_method,
        payment_status="UNPAID", total_cents=total, source_key=source_key,
        risk_score=None, risk_reasons=[], items=resolved, revision=0,
        created_at=now(), updated_at=now(), shipped_at=None, cancelled_reason=None))
    log(db, order_id, actor, "draft_created", total_cents=total)
    return order_view(db, order_id)


@atomic
def create_draft(db, phone, name, items, delivery_address, payment_method, source_key):
    return _draft(db, phone, name, items, delivery_address, payment_method, "owner", source_key, "owner")


def _edit_draft(db, order_id, items, address, actor, requester_phone=None, payment_method=None):
    order = order_view(db, order_id, requester_phone)
    if order["status"] != "DRAFT":
        raise OrderError("Only a draft can be edited.", 409)
    method = payment_method if payment_method is not None else order["payment_method"]
    validate_delivery(address, method)
    resolved, total = resolve_items(db, items)
    touch(db, order_id, delivery_address=address.strip(), payment_method=method,
          total_cents=total, items=resolved)
    log(db, order_id, actor, "draft_edited", total_cents=total)
    return order_view(db, order_id)


@atomic
def edit_draft(db, order_id, items, address, payment_method=None):
    return _edit_draft(db, order_id, items, address, "owner", payment_method=payment_method)


def _risk(db, order):
    reasons, score = [], 0
    if order["total_cents"] >= int(os.getenv("SHOPSYNC_RISK_AMOUNT_CENTS", "2000000")):
        score += 60
        reasons.append("Order total exceeds configured review amount")
    if sum(i["quantity"] for i in order["items"]) >= 6:
        score += 25
        reasons.append("Large item quantity")
    if db.count("orders", {"customer_id": order["customer_id"], "status": "CANCELLED"}) >= 2:
        score += 25
        reasons.append("Two or more previous cancellations")
    return min(score, 100), reasons or ["No configured review indicators matched"]


def movement(db, order_id, item, kind, amount):
    db.insert("stock_movements", dict(id=uid(), order_id=order_id, product_id=item["product_id"],
                                     kind=kind, change_qty=amount, created_at=now()))


def financial_entry(db, order, kind, amount):
    db.insert("financial_entries", dict(id=uid(), order_id=order["id"], kind=kind,
                                       amount_cents=amount, created_at=now()))


def _book(db, order, actor):
    for item in order["items"]:
        result = db.update("products", {"_id": item["product_id"], "active": True,
                                        "stock_qty": {"$gte": item["quantity"]}},
                           {"$inc": {"stock_qty": -item["quantity"]}})
        if result.modified_count != 1:
            raise OrderError(f"Insufficient stock for {item['sku']}; order remains unconfirmed.", 409)
        movement(db, order["id"], item, "CONFIRM", -item["quantity"])
    financial_entry(db, order, "SALE", order["total_cents"])
    touch(db, order["id"], status="CONFIRMED")
    log(db, order["id"], actor, "order_confirmed")


def _confirm(db, order_id, actor, requester_phone=None):
    order = order_view(db, order_id, requester_phone)
    if order["status"] in ("CONFIRMED", "RISK_HOLD"):
        return order
    if order["status"] != "DRAFT":
        raise OrderError("Only a draft can be confirmed.", 409)
    score, reasons = _risk(db, order)
    touch(db, order_id, risk_score=score, risk_reasons=reasons)
    if score >= int(os.getenv("SHOPSYNC_RISK_THRESHOLD", "50")):
        touch(db, order_id, status="RISK_HOLD")
        db.insert("approvals", dict(id=uid(), order_id=order_id, action="HIGH_RISK_ORDER",
                                   status="PENDING", owner_reason=None, created_at=now(), decided_at=None))
        log(db, order_id, actor, "risk_hold", score=score, reasons=reasons)
    else:
        _book(db, order, actor)
    return order_view(db, order_id)


@atomic
def confirm(db, order_id, requester_phone=None):
    return _confirm(db, order_id, "customer" if requester_phone else "owner", requester_phone)


@atomic
def decide_risk(db, order_id, approve, reason):
    order = order_view(db, order_id)
    if order["status"] in ("CONFIRMED", "REJECTED") and order["approval"]:
        return order
    if order["status"] != "RISK_HOLD":
        raise OrderError("This order is not awaiting risk review.", 409)
    if approve:
        _book(db, order, "owner")
    else:
        touch(db, order_id, status="REJECTED")
    db.update("approvals", {"order_id": order_id}, {"$set": {
        "status": "APPROVED" if approve else "REJECTED", "owner_reason": reason.strip(), "decided_at": now()}})
    log(db, order_id, "owner", "risk_decision", approved=approve, reason=reason.strip())
    if order["channel"] == "whatsapp":
        queue_message(db, order["customer_phone"],
                      f"Order {order_id} {'confirmed' if approve else 'rejected after review'}." +
                      (f" Reason: {reason.strip()}" if not approve and reason.strip() else ""))
    return order_view(db, order_id)


def _cancel(db, order_id, actor, requester_phone=None):
    order = order_view(db, order_id, requester_phone)
    if order["status"] == "CANCELLED":
        return order
    if order["status"] not in ("DRAFT", "RISK_HOLD", "CONFIRMED") or order["shipped_at"]:
        raise OrderError("Only an unshipped order can be cancelled.", 409)
    request = order["return_request"]
    if request and request["status"] not in ("REJECTED", "REJECTED_AFTER_INSPECTION"):
        raise OrderError("An active or completed return/refund prevents cancellation.", 409)
    if order["status"] == "CONFIRMED":
        for item in order["items"]:
            db.update("products", {"_id": item["product_id"]}, {"$inc": {"stock_qty": item["quantity"]}})
            movement(db, order_id, item, "CANCEL", item["quantity"])
        financial_entry(db, order, "CANCELLATION", -order["total_cents"])
    touch(db, order_id, status="CANCELLED", cancelled_reason="Requested by customer or owner")
    db.update("approvals", {"order_id": order_id, "status": "PENDING"},
              {"$set": {"status": "CANCELLED", "decided_at": now()}})
    log(db, order_id, actor, "order_cancelled")
    if actor == "owner" and order["channel"] == "whatsapp":
        queue_message(db, order["customer_phone"], f"Order {order_id} has been cancelled.")
    return order_view(db, order_id)


@atomic
def cancel(db, order_id, requester_phone=None):
    return _cancel(db, order_id, "customer" if requester_phone else "owner", requester_phone)


@atomic
def mark_shipped(db, order_id):
    order = order_view(db, order_id)
    if order["status"] == "SHIPPED":
        return order
    if order["status"] != "CONFIRMED":
        raise OrderError("Only a confirmed order can be marked shipped.", 409)
    if order["return_request"] and order["return_request"]["status"] != "REJECTED":
        raise OrderError("Resolve the return/refund before shipping this order.", 409)
    touch(db, order_id, status="SHIPPED", shipped_at=now())
    log(db, order_id, "owner", "order_shipped")
    if order["channel"] == "whatsapp":
        queue_message(db, order["customer_phone"], f"Order {order_id} has been marked shipped.")
    return order_view(db, order_id)


def _request_return(db, order_id, kind, reason, actor, requester_phone=None):
    order = order_view(db, order_id, requester_phone)
    existing = order["return_request"]
    if existing:
        if existing["kind"] == kind and existing["reason"] == reason.strip():
            return order
        raise OrderError("A return or refund request already exists for this order.", 409)
    if order["status"] not in ("CONFIRMED", "SHIPPED"):
        raise OrderError("Only confirmed or shipped orders qualify.", 409)
    if kind not in ("RETURN", "REFUND") or not reason.strip():
        raise OrderError("Choose RETURN or REFUND and give a reason.")
    window_start = datetime.fromisoformat(order["shipped_at"] or order["created_at"])
    if datetime.now(timezone.utc) > window_start + timedelta(days=int(os.getenv("SHOPSYNC_RETURN_DAYS", "14"))):
        raise OrderError("Request is outside the return window.", 409)
    db.insert("return_requests", dict(id=uid(), order_id=order_id, kind=kind, reason=reason.strip()[:500],
                                     status="PENDING", owner_reason=None, created_at=now(),
                                     decided_at=None, received_at=None))
    touch(db, order_id)
    log(db, order_id, actor, "return_requested", kind=kind)
    return order_view(db, order_id)


@atomic
def request_return(db, order_id, kind, reason, requester_phone=None):
    return _request_return(db, order_id, kind, reason, "customer" if requester_phone else "owner", requester_phone)


def _refund_entry(db, order):
    financial_entry(db, order, "REFUND", -order["total_cents"])
    touch(db, order["id"], payment_status="REFUND_REQUIRES_MANUAL_ACTION")


@atomic
def decide_return(db, request_id, approve, reason):
    row = db.get("return_requests", {"_id": request_id})
    if not row:
        raise OrderError("Request not found.", 404)
    order = order_view(db, row["order_id"])
    if row["status"] != "PENDING":
        return order
    if order["status"] not in ("CONFIRMED", "SHIPPED"):
        raise OrderError("This order no longer qualifies for a return/refund decision.", 409)
    if not approve and not reason.strip():
        raise OrderError("A rejection reason is required for a return or refund.")
    status = "REJECTED"
    if approve:
        status = "APPROVED_AWAITING_RECEIPT" if row["kind"] == "RETURN" else "COMPLETED"
        if row["kind"] == "REFUND":
            _refund_entry(db, order)
    db.update("return_requests", {"_id": request_id},
              {"$set": {"status": status, "owner_reason": reason.strip(), "decided_at": now()}})
    touch(db, order["id"])
    log(db, order["id"], "owner", "return_decision", approved=approve, kind=row["kind"], reason=reason.strip())
    if order["channel"] == "whatsapp":
        queue_message(db, order["customer_phone"],
                      f"Your {row['kind'].lower()} request for {order['id']} is {status.lower().replace('_', ' ')}." +
                      (f" Reason: {reason.strip()}" if not approve else ""))
    return order_view(db, order["id"])


@atomic
def receive_return(db, request_id, accept, reason=""):
    row = db.get("return_requests", {"_id": request_id})
    if not row:
        raise OrderError("Request not found.", 404)
    order = order_view(db, row["order_id"])
    if row["status"] in ("COMPLETED", "REJECTED_AFTER_INSPECTION"):
        return order
    if row["kind"] != "RETURN" or row["status"] != "APPROVED_AWAITING_RECEIPT":
        raise OrderError("Return is not awaiting item inspection.", 409)
    if order["status"] not in ("CONFIRMED", "SHIPPED"):
        raise OrderError("This order no longer qualifies for return inspection.", 409)
    if not accept and not reason.strip():
        raise OrderError("A reason is required when rejecting a returned item.")
    if accept:
        for item in order["items"]:
            db.update("products", {"_id": item["product_id"]}, {"$inc": {"stock_qty": item["quantity"]}})
            movement(db, order["id"], item, "RETURN", item["quantity"])
        _refund_entry(db, order)
    status = "COMPLETED" if accept else "REJECTED_AFTER_INSPECTION"
    db.update("return_requests", {"_id": request_id}, {"$set": {
        "status": status, "owner_reason": row["owner_reason"] if accept else reason.strip(), "received_at": now()}})
    touch(db, order["id"])
    log(db, order["id"], "owner", "return_inspected", accepted=accept, reason=reason.strip())
    if order["channel"] == "whatsapp":
        queue_message(db, order["customer_phone"], f"Return for {order['id']} {status.lower().replace('_', ' ')}." +
                      (f" Reason: {reason.strip()}" if not accept else ""))
    return order_view(db, order["id"])


def financial_summary():
    entries = get_store().all("financial_entries", sort=[("created_at", -1), ("_id", -1)])
    return {"entries": entries, "booked_net_cents": sum(x["amount_cents"] for x in entries)}


def audit_log():
    return get_store().all("audit_events", sort=[("created_at", -1), ("_id", -1)], limit=200)


def _chat_operation(db, phone, text, name, live, message_id):
    parts = text.strip().split(maxsplit=2)
    cmd = parts[0].upper() if parts else ""
    if cmd in ("ORDER", "EDIT"):
        pattern = (r"ORDER\s+(\S+)\s+(\d+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)" if cmd == "ORDER" else
                   r"EDIT\s+(\S+)\s+(\S+)\s+(\d+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)")
        match = re.fullmatch(pattern, text.strip(), re.I | re.S)
        if not match:
            if cmd == "ORDER":
                raise OrderError("Choose payment method: ORDER SKU QUANTITY | delivery address | COD or BANK_TRANSFER")
            raise OrderError("Use: EDIT ORDER_ID SKU QUANTITY | delivery address | COD or BANK_TRANSFER")
        values = match.groups()
        sku, quantity, address, payment = values[-4:]
        if len(quantity) > 3:
            raise OrderError("Quantity must be between 1 and 100.")
        product = db.get("products", {"sku": sku.upper(), "active": True})
        if not product:
            raise OrderError("Unknown SKU. Ask the owner for the product code.", 404)
        items = [{"product_id": product["id"], "quantity": int(quantity)}]
        method = payment.strip().upper().replace(" ", "_")
        if cmd == "ORDER":
            order = _draft(db, phone, name, items, address, method,
                           "whatsapp" if live else "simulation", "chat:" + message_id, "customer")
        else:
            order = _edit_draft(db, values[0].upper(), items, address, "customer", phone, method)
        return draft_summary(order)
    if cmd in ("CONFIRM", "STATUS", "CANCEL") and len(parts) >= 2:
        order_id = parts[1].upper()
        if cmd == "CONFIRM":
            order = _confirm(db, order_id, "customer", phone)
        elif cmd == "CANCEL":
            order = _cancel(db, order_id, "customer", phone)
        else:
            order = order_view(db, order_id, phone)
        reply = f"Order {order_id}: {order['status']}."
        if order["status"] == "RISK_HOLD":
            reply += " Awaiting owner review; no stock or sale has been recorded."
        request = order["return_request"]
        if request:
            reply += f" {request['kind'].title()} request: {request['status'].replace('_', ' ').lower()}."
            if request["status"] in ("REJECTED", "REJECTED_AFTER_INSPECTION") and request["owner_reason"]:
                reply += f" Reason: {request['owner_reason']}"
        return reply
    if cmd in ("RETURN", "REFUND") and len(parts) == 3:
        order = _request_return(db, parts[1].upper(), cmd, parts[2], "customer", phone)
        return f"{cmd.title()} request for {order['id']} recorded for owner review."
    return ("Commands: ORDER SKU QTY | address | COD or BANK_TRANSFER; "
            "EDIT ORDER_ID SKU QTY | address | COD or BANK_TRANSFER; CONFIRM ORDER_ID; STATUS ORDER_ID; "
            "CANCEL ORDER_ID; RETURN ORDER_ID reason; REFUND ORDER_ID reason.")


def chat(message_id, phone, text, name="", live=False):
    phone = phone_number(phone)

    def existing(db):
        row = db.get("inbound_messages", {"_id": message_id})
        if row and row["phone"] != phone:
            raise OrderError("Message ID is already used by another sender.", 409)
        return row

    def record(db, reply):
        db.insert("inbound_messages", dict(id=message_id, message_id=message_id, phone=phone,
                                          body=text[:2000], response=reply, created_at=now()))
        if live:
            queue_message(db, phone, reply)
        return reply

    def process(db):
        row = existing(db)
        if row:
            return row["response"]
        customer(db, phone, name, inbound=live)
        reply = _chat_operation(db, phone, text, name, live, message_id)
        return record(db, reply)

    try:
        return run_transaction(process)
    except OrderError as error:
        # MongoDB has no SAVEPOINT. Business changes have been aborted before
        # saving this error response in a NEW transaction.
        error_reply = error.message

        def record_error(db):
            row = existing(db)
            if row:
                return row["response"]
            customer(db, phone, name, inbound=live)
            return record(db, error_reply)
        return run_transaction(record_error)
