import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone

from .db import connect, transaction


class OrderError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid():
    return uuid.uuid4().hex[:16].upper()


def phone_number(value):
    phone = re.sub(r"\D", "", str(value))
    if not 8 <= len(phone) <= 15:
        raise OrderError("Phone must have 8 to 15 digits, including country code.")
    return phone


def log(db, order_id, actor, event, **details):
    db.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?)",
               (uid(), order_id, actor, event, json.dumps(details), now()))


def queue_message(db, phone, body):
    db.execute("INSERT INTO outbox(id,phone,body,status,next_attempt_at,created_at) VALUES(?,?,?,?,?,?)",
               (uid(), phone, body, "PENDING", now(), now()))


def customer(db, phone, name=None, inbound=False):
    phone = phone_number(phone)
    row = db.execute("SELECT * FROM customers WHERE phone=?", (phone,)).fetchone()
    if row:
        if inbound:
            db.execute("UPDATE customers SET last_inbound_at=? WHERE id=?", (now(), row["id"]))
        return row["id"]
    customer_id = uid()
    db.execute("INSERT INTO customers VALUES (?,?,?,?)",
               (customer_id, phone, name or phone, now() if inbound else None))
    return customer_id


def product_list():
    with closing(connect()) as db:
        return [dict(r) for r in db.execute("SELECT * FROM products ORDER BY name")]


def add_product(sku, name, price_cents, stock_qty):
    sku = sku.strip().upper()
    if not sku or not name.strip() or price_cents < 0 or stock_qty < 0:
        raise OrderError("Provide SKU, name, nonnegative price and stock.")
    with transaction() as db:
        try:
            db.execute("INSERT INTO products VALUES (?,?,?,?,?,1)",
                       (uid(), sku, name.strip(), price_cents, stock_qty))
        except sqlite3.IntegrityError:
            raise OrderError("SKU already exists.", 409)
        log(db, None, "owner", "product_created", sku=sku, stock_qty=stock_qty)
        return dict(db.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone())


def restock_product(product_id, quantity):
    if quantity <= 0:
        raise OrderError("Restock quantity must be positive.")
    with transaction() as db:
        result = db.execute("UPDATE products SET stock_qty=stock_qty+? WHERE id=?",
                            (quantity, product_id))
        if not result.rowcount:
            raise OrderError("Product not found.", 404)
        log(db, None, "owner", "manual_restock", product_id=product_id, quantity=quantity)
        return dict(db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone())


def order_view(db, order_id, requester_phone=None):
    order = db.execute("SELECT o.*, c.phone AS customer_phone, c.name AS customer_name "
                       "FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=?", (order_id,)).fetchone()
    if not order or (requester_phone and order["customer_phone"] != phone_number(requester_phone)):
        raise OrderError("Order not found.", 404)
    result = dict(order)
    result["risk_reasons"] = json.loads(result["risk_reasons"] or "[]")
    result["items"] = [dict(r) for r in db.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,))]
    result["approval"] = next((dict(r) for r in db.execute(
        "SELECT * FROM approvals WHERE order_id=?", (order_id,))), None)
    result["return_request"] = next((dict(r) for r in db.execute(
        "SELECT * FROM return_requests WHERE order_id=?", (order_id,))), None)
    return result


def get_order(order_id, requester_phone=None):
    with closing(connect()) as db:
        return order_view(db, order_id, requester_phone)


def list_orders():
    with closing(connect()) as db:
        ids = [r[0] for r in db.execute("SELECT id FROM orders ORDER BY created_at DESC, rowid DESC")]
        return [order_view(db, item) for item in ids]


def _draft(db, phone, name, items, delivery_address, payment_method, channel, source_key, actor):
    existing = db.execute("SELECT id FROM orders WHERE source_key=?", (source_key,)).fetchone()
    if existing:
        return order_view(db, existing["id"])
    if not delivery_address.strip() or len(delivery_address) > 500:
        raise OrderError("Provide a delivery address (maximum 500 characters).")
    if payment_method not in ("COD", "BANK_TRANSFER"):
        raise OrderError("Payment method must be COD or BANK_TRANSFER.")
    if not items or len(items) > 25:
        raise OrderError("An order needs 1 to 25 distinct products.")
    if len({item["product_id"] for item in items}) != len(items):
        raise OrderError("List each product once with its total quantity.")
    resolved, total = [], 0
    for item in items:
        quantity = item["quantity"]
        if not 1 <= quantity <= 100:
            raise OrderError("Each quantity must be between 1 and 100.")
        p = db.execute("SELECT * FROM products WHERE id=? AND active=1", (item["product_id"],)).fetchone()
        if not p:
            raise OrderError("Product not found or inactive.", 404)
        if p["stock_qty"] < quantity:
            raise OrderError(f"Insufficient stock for {p['sku']}.", 409)
        resolved.append((p, quantity))
        total += p["price_cents"] * quantity
    customer_id = customer(db, phone, name)
    order_id = uid()
    db.execute("INSERT INTO orders(id,customer_id,channel,status,delivery_address,payment_method,"
               "total_cents,source_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (order_id, customer_id, channel, "DRAFT", delivery_address.strip(),
                payment_method, total, source_key, now(), now()))
    for p, quantity in resolved:
        db.execute("INSERT INTO order_items VALUES(?,?,?,?,?,?)",
                   (order_id, p["id"], p["sku"], p["name"], p["price_cents"], quantity))
    log(db, order_id, actor, "draft_created", total_cents=total)
    return order_view(db, order_id)


def create_draft(phone, name, items, delivery_address, payment_method, source_key):
    with transaction() as db:
        return _draft(db, phone, name, items, delivery_address, payment_method,
                      "owner", source_key, "owner")


def _edit_draft(db, order_id, items, address, actor, requester_phone=None):
    order = order_view(db, order_id, requester_phone)
    if order["status"] != "DRAFT":
        raise OrderError("Only a draft can be edited.", 409)
    if not address.strip() or len(address) > 500:
        raise OrderError("Provide a delivery address (maximum 500 characters).")
    if not items or len(items) > 25 or len({i["product_id"] for i in items}) != len(items):
        raise OrderError("Provide 1 to 25 distinct products.")
    resolved, total = [], 0
    for item in items:
        qty = item["quantity"]
        p = db.execute("SELECT * FROM products WHERE id=? AND active=1", (item["product_id"],)).fetchone()
        if not p or not 1 <= qty <= 100 or p["stock_qty"] < qty:
            raise OrderError("Invalid product, quantity, or insufficient stock.", 409)
        resolved.append((p, qty))
        total += p["price_cents"] * qty
    db.execute("DELETE FROM order_items WHERE order_id=?", (order_id,))
    for p, qty in resolved:
        db.execute("INSERT INTO order_items VALUES(?,?,?,?,?,?)",
                   (order_id, p["id"], p["sku"], p["name"], p["price_cents"], qty))
    db.execute("UPDATE orders SET delivery_address=?,total_cents=?,updated_at=? WHERE id=?",
               (address.strip(), total, now(), order_id))
    log(db, order_id, actor, "draft_edited", total_cents=total)
    return order_view(db, order_id)


def edit_draft(order_id, items, address):
    with transaction() as db:
        return _edit_draft(db, order_id, items, address, "owner")


def _risk(db, order):
    # Explicit, reproducible review indicators; no claim of detecting fraud.
    reasons, score = [], 0
    if order["total_cents"] >= int(os.getenv("SHOPSYNC_RISK_AMOUNT_CENTS", "2000000")):
        score += 60
        reasons.append("Order total exceeds configured review amount")
    total_qty = db.execute("SELECT SUM(quantity) FROM order_items WHERE order_id=?", (order["id"],)).fetchone()[0]
    if total_qty >= 6:
        score += 25
        reasons.append("Large item quantity")
    previous = db.execute("SELECT COUNT(*) FROM orders WHERE customer_id=? AND status='CANCELLED'",
                          (order["customer_id"],)).fetchone()[0]
    if previous >= 2:
        score += 25
        reasons.append("Two or more previous cancellations")
    return min(score, 100), reasons or ["No configured review indicators matched"]


def _book(db, order, actor):
    # Caller owns BEGIN IMMEDIATE. Order + inventory + sale commit or roll back together.
    for item in db.execute("SELECT * FROM order_items WHERE order_id=?", (order["id"],)):
        result = db.execute("UPDATE products SET stock_qty=stock_qty-? WHERE id=? AND stock_qty>=?",
                            (item["quantity"], item["product_id"], item["quantity"]))
        if result.rowcount != 1:
            raise OrderError(f"Insufficient stock for {item['sku']}; order remains unconfirmed.", 409)
        db.execute("INSERT INTO stock_movements VALUES(?,?,?,?,?,?)",
                   (uid(), order["id"], item["product_id"], "CONFIRM", -item["quantity"], now()))
    db.execute("INSERT INTO financial_entries VALUES(?,?,?,?,?)",
               (uid(), order["id"], "SALE", order["total_cents"], now()))
    db.execute("UPDATE orders SET status='CONFIRMED',updated_at=? WHERE id=?", (now(), order["id"]))
    log(db, order["id"], actor, "order_confirmed")


def _confirm(db, order_id, actor, requester_phone=None):
    order = order_view(db, order_id, requester_phone)
    if order["status"] in ("CONFIRMED", "RISK_HOLD"):
        return order
    if order["status"] != "DRAFT":
        raise OrderError("Only a draft can be confirmed.", 409)
    score, reasons = _risk(db, order)
    db.execute("UPDATE orders SET risk_score=?,risk_reasons=?,updated_at=? WHERE id=?",
               (score, json.dumps(reasons), now(), order_id))
    if score >= int(os.getenv("SHOPSYNC_RISK_THRESHOLD", "50")):
        db.execute("UPDATE orders SET status='RISK_HOLD' WHERE id=?", (order_id,))
        db.execute("INSERT INTO approvals(id,order_id,action,status,created_at) VALUES(?,?,?,?,?)",
                   (uid(), order_id, "HIGH_RISK_ORDER", "PENDING", now()))
        log(db, order_id, actor, "risk_hold", score=score, reasons=reasons)
    else:
        _book(db, order, actor)
    return order_view(db, order_id)


def confirm(order_id, requester_phone=None):
    with transaction() as db:
        return _confirm(db, order_id, "customer" if requester_phone else "owner", requester_phone)


def decide_risk(order_id, approve, reason):
    with transaction() as db:
        order = order_view(db, order_id)
        if order["status"] in ("CONFIRMED", "REJECTED") and order["approval"]:
            return order
        if order["status"] != "RISK_HOLD":
            raise OrderError("This order is not awaiting risk review.", 409)
        if approve:
            _book(db, order, "owner")  # Stock may have changed since the hold.
        else:
            db.execute("UPDATE orders SET status='REJECTED',updated_at=? WHERE id=?", (now(), order_id))
        db.execute("UPDATE approvals SET status=?,owner_reason=?,decided_at=? WHERE order_id=?",
                   ("APPROVED" if approve else "REJECTED", reason.strip(), now(), order_id))
        log(db, order_id, "owner", "risk_decision", approved=approve, reason=reason)
        if order["channel"] == "whatsapp":
            queue_message(db, order["customer_phone"],
                          f"Order {order_id} {'confirmed' if approve else 'rejected after review'}." +
                          (f" Reason: {reason.strip()[:200]}" if not approve and reason.strip() else ""))
        return order_view(db, order_id)


def _cancel(db, order_id, actor, requester_phone=None):
    order = order_view(db, order_id, requester_phone)
    if order["status"] == "CANCELLED":
        return order
    if order["status"] not in ("DRAFT", "RISK_HOLD", "CONFIRMED") or order["shipped_at"]:
        raise OrderError("Only an unshipped order can be cancelled.", 409)
    if order["status"] == "CONFIRMED":
        for item in order["items"]:
            db.execute("UPDATE products SET stock_qty=stock_qty+? WHERE id=?",
                       (item["quantity"], item["product_id"]))
            db.execute("INSERT INTO stock_movements VALUES(?,?,?,?,?,?)",
                       (uid(), order_id, item["product_id"], "CANCEL", item["quantity"], now()))
        db.execute("INSERT INTO financial_entries VALUES(?,?,?,?,?)",
                   (uid(), order_id, "CANCELLATION", -order["total_cents"], now()))
    db.execute("UPDATE orders SET status='CANCELLED',cancelled_reason='Requested by customer or owner',updated_at=? WHERE id=?",
               (now(), order_id))
    db.execute("UPDATE approvals SET status='CANCELLED',decided_at=? WHERE order_id=? AND status='PENDING'",
               (now(), order_id))
    log(db, order_id, actor, "order_cancelled")
    if actor == "owner" and order["channel"] == "whatsapp":
        queue_message(db, order["customer_phone"], f"Order {order_id} has been cancelled.")
    return order_view(db, order_id)


def cancel(order_id, requester_phone=None):
    with transaction() as db:
        return _cancel(db, order_id, "customer" if requester_phone else "owner", requester_phone)


def mark_shipped(order_id):
    with transaction() as db:
        order = order_view(db, order_id)
        if order["status"] == "SHIPPED":
            return order
        if order["status"] != "CONFIRMED":
            raise OrderError("Only a confirmed order can be marked shipped.", 409)
        db.execute("UPDATE orders SET status='SHIPPED',shipped_at=?,updated_at=? WHERE id=?",
                   (now(), now(), order_id))
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
    db.execute("INSERT INTO return_requests(id,order_id,kind,reason,status,created_at) VALUES(?,?,?,?,?,?)",
               (uid(), order_id, kind, reason.strip()[:500], "PENDING", now()))
    log(db, order_id, actor, "return_requested", kind=kind)
    return order_view(db, order_id)


def request_return(order_id, kind, reason, requester_phone=None):
    with transaction() as db:
        return _request_return(db, order_id, kind, reason,
                               "customer" if requester_phone else "owner", requester_phone)


def _refund_entry(db, order):
    # Bookkeeping reversal only; the owner performs any real gateway refund separately.
    db.execute("INSERT INTO financial_entries VALUES(?,?,?,?,?)",
               (uid(), order["id"], "REFUND", -order["total_cents"], now()))
    db.execute("UPDATE orders SET payment_status='REFUND_REQUIRES_MANUAL_ACTION',updated_at=? WHERE id=?",
               (now(), order["id"]))


def decide_return(request_id, approve, reason):
    with transaction() as db:
        row = db.execute("SELECT * FROM return_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise OrderError("Request not found.", 404)
        order = order_view(db, row["order_id"])
        if row["status"] != "PENDING":
            return order
        status = "REJECTED"
        if approve:
            status = "APPROVED_AWAITING_RECEIPT" if row["kind"] == "RETURN" else "COMPLETED"
            if row["kind"] == "REFUND":
                _refund_entry(db, order)
        db.execute("UPDATE return_requests SET status=?,owner_reason=?,decided_at=? WHERE id=?",
                   (status, reason.strip(), now(), request_id))
        log(db, order["id"], "owner", "return_decision", approved=approve, kind=row["kind"])
        if order["channel"] == "whatsapp":
            queue_message(db, order["customer_phone"],
                          f"Your {row['kind'].lower()} request for {order['id']} is {status.lower().replace('_',' ')}." +
                          (f" Reason: {reason.strip()[:200]}" if not approve and reason.strip() else ""))
        return order_view(db, order["id"])


def receive_return(request_id, accept):
    with transaction() as db:
        row = db.execute("SELECT * FROM return_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise OrderError("Request not found.", 404)
        order = order_view(db, row["order_id"])
        if row["status"] in ("COMPLETED", "REJECTED_AFTER_INSPECTION"):
            return order
        if row["kind"] != "RETURN" or row["status"] != "APPROVED_AWAITING_RECEIPT":
            raise OrderError("Return is not awaiting item inspection.", 409)
        if accept:
            for item in order["items"]:
                db.execute("UPDATE products SET stock_qty=stock_qty+? WHERE id=?",
                           (item["quantity"], item["product_id"]))
                db.execute("INSERT INTO stock_movements VALUES(?,?,?,?,?,?)",
                           (uid(), order["id"], item["product_id"], "RETURN", item["quantity"], now()))
            _refund_entry(db, order)
        status = "COMPLETED" if accept else "REJECTED_AFTER_INSPECTION"
        db.execute("UPDATE return_requests SET status=?,received_at=? WHERE id=?",
                   (status, now(), request_id))
        log(db, order["id"], "owner", "return_inspected", accepted=accept)
        if order["channel"] == "whatsapp":
            queue_message(db, order["customer_phone"],
                          f"Return for {order['id']} {status.lower().replace('_',' ')}.")
        return order_view(db, order["id"])


def financial_summary():
    with closing(connect()) as db:
        entries = [dict(r) for r in db.execute("SELECT * FROM financial_entries ORDER BY created_at DESC")]
        return {"entries": entries, "booked_net_cents": sum(x["amount_cents"] for x in entries)}


def audit_log():
    with closing(connect()) as db:
        return [dict(r) for r in db.execute("SELECT * FROM audit_events ORDER BY created_at DESC, rowid DESC LIMIT 200")]


def chat(message_id, phone, text, name="", live=False):
    phone = phone_number(phone)
    with transaction() as db:
        old = db.execute("SELECT response FROM inbound_messages WHERE message_id=?", (message_id,)).fetchone()
        if old:
            return old["response"]
        customer(db, phone, name, inbound=live)
        parts = text.strip().split(maxsplit=2)
        cmd = parts[0].upper() if parts else ""
        # A failed multi-product confirmation may have written one stock row.
        # Roll back the command while still remembering the error reply for webhook deduplication.
        db.execute("SAVEPOINT chat_operation")
        try:
            if cmd == "ORDER":
                match = re.fullmatch(r"ORDER\s+(\S+)\s+(\d+)\s*\|\s*(.+)", text.strip(), re.I | re.S)
                if not match:
                    raise OrderError("Use: ORDER SKU QUANTITY | delivery address")
                sku, quantity, address = match.groups()
                if len(quantity) > 3:
                    raise OrderError("Quantity must be between 1 and 100.")
                p = db.execute("SELECT id FROM products WHERE sku=? AND active=1", (sku.upper(),)).fetchone()
                if not p:
                    raise OrderError("Unknown SKU. Ask the owner for the product code.", 404)
                order = _draft(db, phone, name, [{"product_id": p["id"], "quantity": int(quantity)}],
                               address, "COD", "whatsapp" if live else "simulation", "chat:" + message_id,
                               "customer")
                reply = (f"Draft {order['id']}: {quantity} x {sku.upper()}, total PKR "
                         f"{order['total_cents']/100:.2f}. Reply CONFIRM {order['id']} to place it.")
            elif cmd == "EDIT":
                match = re.fullmatch(r"EDIT\s+(\S+)\s+(\S+)\s+(\d+)\s*\|\s*(.+)", text.strip(), re.I | re.S)
                if not match:
                    raise OrderError("Use: EDIT ORDER_ID SKU QUANTITY | delivery address")
                order_id, sku, quantity, address = match.groups()
                if len(quantity) > 3:
                    raise OrderError("Quantity must be between 1 and 100.")
                p = db.execute("SELECT id FROM products WHERE sku=? AND active=1", (sku.upper(),)).fetchone()
                if not p:
                    raise OrderError("Unknown SKU.", 404)
                edited = _edit_draft(db, order_id.upper(), [{"product_id": p["id"], "quantity": int(quantity)}],
                                     address, "customer", phone)
                reply = f"Draft {edited['id']} updated. New total PKR {edited['total_cents']/100:.2f}. Reply CONFIRM {edited['id']}."
            elif cmd in ("CONFIRM", "STATUS", "CANCEL") and len(parts) >= 2:
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
            elif cmd in ("RETURN", "REFUND") and len(parts) == 3:
                order = _request_return(db, parts[1].upper(), cmd, parts[2], "customer", phone)
                reply = f"{cmd.title()} request for {order['id']} recorded for owner review."
            else:
                reply = ("Commands: ORDER SKU QTY | address; EDIT ORDER_ID SKU QTY | address; CONFIRM ORDER_ID; STATUS ORDER_ID; "
                         "CANCEL ORDER_ID; RETURN ORDER_ID reason; REFUND ORDER_ID reason.")
        except OrderError as exc:
            db.execute("ROLLBACK TO SAVEPOINT chat_operation")
            reply = exc.message
        finally:
            db.execute("RELEASE SAVEPOINT chat_operation")
        db.execute("INSERT INTO inbound_messages VALUES (?,?,?,?,?)",
                   (message_id, phone, text[:2000], reply, now()))
        if live:
            queue_message(db, phone, reply)
        return reply
