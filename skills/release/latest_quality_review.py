"""Order the trusted App's submitted reviews from native GitHub pages."""

from __future__ import annotations

from datetime import datetime
import json
import re
import sys


def submitted_quality_reviews(raw: str, login: str) -> list[dict]:
    # gh api --paginate emits consecutive JSON arrays. Read the original number
    # tokens before jq can normalize an integral float or round a large ID.
    if not raw.strip():
        raise ValueError("Missing review page")
    decoder = json.JSONDecoder()
    offset = 0
    seen = set()
    submitted_reviews = []
    while offset < len(raw):
        if raw[offset].isspace():
            offset += 1
            continue
        page, offset = decoder.raw_decode(raw, offset)
        if not isinstance(page, list):
            raise ValueError("Invalid review page")
        for review in page:
            if not isinstance(review, dict):
                raise ValueError("Invalid native review row")
            review_id = review.get("id")
            # Pagination can repeat an ID even when attribution differs.
            if type(review_id) is not int or review_id <= 0:
                raise ValueError("Invalid native review ID")
            if review_id in seen:
                raise ValueError("Repeated native review ID")
            seen.add(review_id)
            if "user" not in review:
                raise ValueError("Missing review attribution")
            actor = review["user"]
            if actor is None:
                continue
            actor_login = actor.get("login") if isinstance(actor, dict) else None
            if (not isinstance(actor_login, str) or not 0 < len(actor_login) <= 100
                    or any(ord(c) < 32 or ord(c) == 127 for c in actor_login)):
                raise ValueError("Invalid review attribution")
            if actor_login != login:
                continue
            if review.get("state") == "PENDING":
                continue
            submitted = review.get("submitted_at")
            if not isinstance(submitted, str) or not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z", submitted
            ):
                raise ValueError("Invalid quality submission time")
            try:
                timestamp = datetime.strptime(submitted, "%Y-%m-%dT%H:%M:%S.%fZ" if "." in submitted else "%Y-%m-%dT%H:%M:%SZ")
            except ValueError as error:
                raise ValueError("Invalid quality submission date") from error
            submitted_reviews.append((timestamp, review_id, review))
    submitted_reviews.sort(key=lambda item: (item[0], item[1]))
    return [review for _, _, review in submitted_reviews]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 latest_quality_review.py REVIEWER_LOGIN < review-pages.json")
    try:
        print(json.dumps(submitted_quality_reviews(sys.stdin.read(), sys.argv[1])))
    except json.JSONDecodeError:
        sys.exit("Unreadable review page — inspect the paginated GitHub reviews and retry")
    except ValueError as error:
        sys.exit(f"{error} — inspect the submitted GitHub reviews and retry")
