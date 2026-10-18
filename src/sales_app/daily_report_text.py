"""The words of a daily report: figures (a plain dict, see `sales_app.daily_report`) in, text out.

Pure, no I/O, no secret. English is the reference; French and Arabic are the other languages a
client can choose. A figure the platform does not track is said as "not tracked", never as zero, and
nothing here ever calls an intention or an appointment a sale.
"""

from collections.abc import Mapping
from typing import Any

LANGUAGES = ("en", "fr", "ar")

_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Daily report {day}",
        "period": "Period: {start} to {end} ({tz})",
        "prospects": "Prospects: {total} in total ({by_status})",
        "sent": "Messages sent: {sent} to {people} prospect(s): {new} new contact(s), "
        "{follow_ups} follow-up(s), {replies} answer(s)",
        "received": "Replies received: {received} from {people} prospect(s)",
        "scheduled": "Follow-ups scheduled: {scheduled} ({soon} due within 24 hours)",
        "untracked": "Purchase intentions, appointments and conversions are not tracked by the "
        "platform: none is reported, and nothing here is a confirmed sale.",
        "quota": "Email quota (UTC day {day}): {used} used of {cap}, {remaining} left",
        "quota_none": "Email quota: not configured by the administrator",
        "quota_unavailable": "Email quota: cannot be read right now",
        "health_ok": "Agent status: no problem reported since the last restart",
        "health_bad": "Agent status: problems reported ({detail})",
        "actions": "Actions required:",
        "actions_none": "Actions required: none",
        "needs_human": "{count} prospect(s) wait for a person to take over",
        "send_cap_not_configured": "The daily email limit is not set: contact your administrator",
        "send_cap_reached": "The daily email limit is reached: no more email goes out today",
        "channel_degraded": "The agent reported errors: check your channel and your source",
        "sync_failing": "Results could not be written back to your source",
        "inbox_failing": "Your mailbox could not be read",
        "unknown": "unknown",
    },
    "fr": {
        "title": "Rapport quotidien {day}",
        "period": "Periode : du {start} au {end} ({tz})",
        "prospects": "Prospects : {total} au total ({by_status})",
        "sent": "Messages envoyes : {sent} a {people} prospect(s) : {new} nouveau(x) contact(s), "
        "{follow_ups} relance(s), {replies} reponse(s)",
        "received": "Reponses recues : {received} de {people} prospect(s)",
        "scheduled": "Relances planifiees : {scheduled} ({soon} dans les 24 heures)",
        "untracked": "Les intentions d'achat, rendez-vous et conversions ne sont pas suivis par la "
        "plateforme : aucun n'est rapporte, et rien ici n'est une vente confirmee.",
        "quota": "Quota email (jour UTC {day}) : {used} utilises sur {cap}, {remaining} restants",
        "quota_none": "Quota email : non configure par l'administrateur",
        "quota_unavailable": "Quota email : illisible pour le moment",
        "health_ok": "Etat de l'agent : aucun probleme signale depuis le dernier redemarrage",
        "health_bad": "Etat de l'agent : problemes signales ({detail})",
        "actions": "Actions requises :",
        "actions_none": "Actions requises : aucune",
        "needs_human": "{count} prospect(s) attendent qu'une personne reprenne la main",
        "send_cap_not_configured": "La limite quotidienne d'emails n'est pas definie : "
        "contactez votre administrateur",
        "send_cap_reached": "La limite quotidienne d'emails est atteinte : plus aucun email "
        "aujourd'hui",
        "channel_degraded": "L'agent a signale des erreurs : verifiez votre canal et votre source",
        "sync_failing": "Les resultats n'ont pas pu etre ecrits dans votre source",
        "inbox_failing": "Votre boite mail n'a pas pu etre lue",
        "unknown": "inconnu",
    },
    "ar": {
        "title": "التقرير اليومي {day}",
        "period": "الفترة: من {start} إلى {end} ({tz})",
        "prospects": "العملاء المحتملون: {total} في المجموع ({by_status})",
        "sent": "الرسائل المرسلة: {sent} إلى {people} عميل: {new} اتصال جديد، "
        "{follow_ups} متابعة، {replies} رد",
        "received": "الردود المستلمة: {received} من {people} عميل",
        "scheduled": "المتابعات المجدولة: {scheduled} ({soon} خلال 24 ساعة)",
        "untracked": "لا تتتبع المنصة نوايا الشراء أو المواعيد أو التحويلات: لا يُبلَّغ عن أي منها، "
        "ولا شيء هنا يمثل بيعاً مؤكداً.",
        "quota": "حصة البريد (يوم UTC {day}): {used} مستخدمة من {cap}، المتبقي {remaining}",
        "quota_none": "حصة البريد: لم يحددها المسؤول",
        "quota_unavailable": "حصة البريد: لا يمكن قراءتها حالياً",
        "health_ok": "حالة الوكيل: لا مشكلة مبلغ عنها منذ آخر إعادة تشغيل",
        "health_bad": "حالة الوكيل: مشاكل مبلغ عنها ({detail})",
        "actions": "إجراءات مطلوبة:",
        "actions_none": "إجراءات مطلوبة: لا شيء",
        "needs_human": "{count} عميل ينتظر تدخل شخص",
        "send_cap_not_configured": "الحد اليومي للبريد غير محدد: اتصل بالمسؤول",
        "send_cap_reached": "تم بلوغ الحد اليومي للبريد: لن يُرسل بريد آخر اليوم",
        "channel_degraded": "أبلغ الوكيل عن أخطاء: تحقق من قناتك ومصدرك",
        "sync_failing": "تعذّرت كتابة النتائج في مصدرك",
        "inbox_failing": "تعذّرت قراءة صندوق بريدك",
        "unknown": "غير معروف",
    },
}


def _stamp(value: str, language: str) -> str:
    """`2026-10-17T08:00:00+00:00` -> `2026-10-17 08:00` (the instant, already in the zone)."""
    return value[:16].replace("T", " ")


def render_text(figures: Mapping[str, Any], language: str) -> str:
    t = _TEXTS.get(language, _TEXTS["en"])
    period = figures["period"]
    prospects = figures["prospects"]
    activity = figures["activity"]
    follow = figures["follow_ups"]
    by_status = ", ".join(f"{k} {v}" for k, v in prospects["by_status"].items() if v) or "0"
    lines = [
        t["title"].format(day=period["day"]),
        t["period"].format(
            start=_stamp(period["local_start"], language),
            end=_stamp(period["local_end"], language),
            tz=period["timezone"],
        ),
        "",
        t["prospects"].format(total=prospects["total"], by_status=by_status),
        t["sent"].format(
            sent=activity["messages_sent"],
            people=activity["prospects_contacted"],
            new=activity["new_contacts"],
            follow_ups=activity["follow_ups_sent"],
            replies=activity["answers_sent"],
        ),
        t["received"].format(
            received=activity["replies_received"], people=activity["prospects_replied"]
        ),
        t["scheduled"].format(scheduled=follow["scheduled"], soon=follow["due_next_24h"]),
        t["untracked"],
        "",
    ]
    quota = figures.get("quota")
    if quota is None or quota.get("status") == "unavailable":
        lines.append(t["quota_unavailable"])
    elif quota.get("status") == "not_configured":
        lines.append(t["quota_none"])
    else:
        lines.append(
            t["quota"].format(
                day=quota["day_utc"],
                used=quota["used"],
                cap=quota["cap"],
                remaining=quota["remaining"],
            )
        )
    health = figures.get("health")
    problems = health_problems(health)
    if problems:
        lines.append(t["health_bad"].format(detail=", ".join(problems)))
    else:
        lines.append(t["health_ok"])
    lines.append("")
    required = figures.get("actions_required") or []
    if not required:
        lines.append(t["actions_none"])
    else:
        lines.append(t["actions"])
        for item in required:
            sentence = t.get(item["code"], item["code"])
            lines.append("- " + sentence.format(count=item.get("count", 0)))
    return "\n".join(lines)


def health_problems(health: Mapping[str, Any] | None) -> list[str]:
    if not health:
        return []
    found = []
    if health.get("consecutive_failures"):
        found.append(f"jobs {health['consecutive_failures']}")
    if health.get("inbox_failures"):
        found.append(f"inbox {health['inbox_failures']}")
    if health.get("sync_failures"):
        found.append(f"sync {health['sync_failures']}")
    return found
