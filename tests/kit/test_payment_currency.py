"""An offline buyer is told what the price comes to in the currency they pay in, at today's rate;
a buyer paying online keeps the price as configured. The amount is worked out by code."""

from copy import deepcopy
from dataclasses import replace

from brain_support import request
from commerce_support import ONGOING, at_payment, commerce_doc

from sales_brain_kit import parse_settings
from sales_brain_kit.consultative import build_context
from sales_brain_kit.consultative.commerce import exact_values

RATES = {"USD": 1.0, "EUR": 0.9, "TND": 3.0}


def payment_texts(rates, *, pay_currency="TND", language="en"):
    doc = deepcopy(commerce_doc())
    for method in doc["payment"]["methods"]:
        if method["id"] in ("bank-transfer", "online-card"):
            method.setdefault("metadata", {})["pay_currency"] = pay_currency
    ctx = build_context(
        replace(request(facts=at_payment(), **ONGOING), exchange_rates=rates), parse_settings(doc)
    )
    return {p.ref: v.value for p, v in exact_values(ctx, language).items() if p.kind == "payment"}


def test_an_offline_method_gives_the_price_in_the_buyers_currency_at_todays_rate():
    text = payment_texts(RATES)["bank-transfer"]
    assert text.startswith("Price: 85.5+ EUR, about 285+ TND at today's rate.\n")
    assert "Transfer to:" in text


def test_the_sentence_follows_the_language_of_the_message():
    assert payment_texts(RATES, language="fr")["bank-transfer"].startswith(
        "Prix : 85.5+ EUR, soit environ"
    )


def test_an_online_method_keeps_the_price_as_configured():
    assert "TND" not in payment_texts(RATES)["online-card"]


def test_without_a_rate_nothing_is_converted_and_the_instruction_is_unchanged():
    text = payment_texts({})["bank-transfer"]
    assert "TND" not in text and text.startswith("Transfer to:")


def test_the_same_currency_needs_no_conversion():
    assert "about" not in payment_texts(RATES, pay_currency="EUR")["bank-transfer"]
