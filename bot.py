"""Telegram bot entrypoint for the AllDataCars fleet maintenance agent."""
from __future__ import annotations

import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import agent
import config
import db
import vehicle_lookup
from claude_client import ClaudeError

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("alldatacars")

MAX_MSG = 3900  # Telegram hard limit is 4096; leave headroom for formatting.

HELP_TEXT = (
    "🚗 *AllDataCars* — your fleet maintenance agent.\n\n"
    "I help you service and repair your vehicles: step-by-step guides, repair "
    "videos, and PDF service manuals — powered by Claude via your computer.\n\n"
    "*Commands*\n"
    "/addvehicle `<Make> <Model> <Year>` — register a vehicle\n"
    "/plate `<מספר רכב>` — חיפוש רכב במאגרי data.gov.il והוספה לצי\n"
    "/vehicles — list vehicles and pick the active one\n"
    "/select `<id>` — set the active vehicle\n"
    "/info — show the active vehicle\n"
    "/fsm — show stored FSM docs (send me a PDF to add one)\n"
    "/links — show cached videos & PDF manuals\n"
    "/help — show this help\n\n"
    "Then just *ask me* anything, e.g. _\"How do I replace the front brake pads?\"_"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _authorized(user_id: int) -> bool:
    return not config.ALLOWED_USER_IDS or user_id in config.ALLOWED_USER_IDS


async def _guard(update: Update) -> bool:
    user = update.effective_user
    if user and _authorized(user.id):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("⛔ You are not authorized to use this bot.")
    return False


async def _send_long(update: Update, text: str, **kwargs) -> None:
    """Send text in chunks under Telegram's length limit.

    ``reply_markup`` (if any) is attached only to the final chunk.
    """
    reply_markup = kwargs.pop("reply_markup", None)
    chunks = [text[i : i + MAX_MSG] for i in range(0, len(text), MAX_MSG)] or [""]
    for idx, chunk in enumerate(chunks):
        extra = {"reply_markup": reply_markup} if idx == len(chunks) - 1 and reply_markup else {}
        await update.effective_message.reply_text(chunk, **kwargs, **extra)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def cmd_addvehicle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /addvehicle <Make> <Model> <Year>\n"
            "Example: /addvehicle Toyota Corolla 2015"
        )
        return

    make = args[0]
    model = args[1] if len(args) > 1 else ""
    year = args[2] if len(args) > 2 else ""
    name = " ".join(args).strip()

    user_id = update.effective_user.id
    vehicle_id = db.add_vehicle(user_id, name=name, make=make, model=model, year=year)
    db.set_active_vehicle(user_id, vehicle_id)
    await update.message.reply_text(
        f"✅ Added *{name}* (id `{vehicle_id}`) and set it as active.\n"
        "Send me a PDF to attach its service manual, or just ask a maintenance question.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_vehicles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    user_id = update.effective_user.id
    vehicles = db.list_vehicles(user_id)
    if not vehicles:
        await update.message.reply_text("No vehicles yet. Add one with /addvehicle.")
        return

    active = db.get_active_vehicle(user_id)
    active_id = active["id"] if active else None
    buttons = [
        [InlineKeyboardButton(
            ("✅ " if v["id"] == active_id else "") + f"{v['name']} (#{v['id']})",
            callback_data=f"select:{v['id']}",
        )]
        for v in vehicles
    ]
    await update.message.reply_text(
        "Your fleet — tap to set the active vehicle:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cmd_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /select <vehicle id>")
        return
    try:
        vehicle_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("The id must be a number. See /vehicles.")
        return

    user_id = update.effective_user.id
    vehicle = db.get_vehicle(vehicle_id)
    if not vehicle or vehicle["user_id"] != user_id:
        await update.message.reply_text("No such vehicle. See /vehicles.")
        return
    db.set_active_vehicle(user_id, vehicle_id)
    await update.message.reply_text(f"✅ Active vehicle: *{vehicle['name']}*", parse_mode=ParseMode.MARKDOWN)


async def on_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not _authorized(user_id):
        return
    vehicle_id = int(query.data.split(":", 1)[1])
    vehicle = db.get_vehicle(vehicle_id)
    if not vehicle or vehicle["user_id"] != user_id:
        await query.edit_message_text("No such vehicle.")
        return
    db.set_active_vehicle(user_id, vehicle_id)
    await query.edit_message_text(f"✅ Active vehicle: {vehicle['name']}")


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    vehicle = db.get_active_vehicle(update.effective_user.id)
    if not vehicle:
        await update.message.reply_text("No active vehicle. Use /vehicles or /addvehicle.")
        return
    fields = ["make", "model", "year", "engine", "plate", "vin", "notes"]
    lines = [f"*{vehicle['name']}* (id `{vehicle['id']}`)"]
    lines += [f"- {f.capitalize()}: {vehicle[f]}" for f in fields if vehicle.get(f)]
    fsm_count = len(db.get_fsm_docs(vehicle["id"]))
    link_count = len(db.get_cached_links(vehicle["id"]))
    lines.append(f"- FSM docs: {fsm_count} | cached links: {link_count}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_fsm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    vehicle = db.get_active_vehicle(update.effective_user.id)
    if not vehicle:
        await update.message.reply_text("No active vehicle. Use /vehicles or /addvehicle.")
        return
    docs = db.get_fsm_docs(vehicle["id"])
    if not docs:
        await update.message.reply_text(
            "No FSM documents stored. Send me a PDF service manual to attach it to "
            f"*{vehicle['name']}*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    lines = [f"📚 FSM docs for *{vehicle['name']}*:"]
    for d in docs:
        size = len(d.get("content_text") or "")
        lines.append(f"- {d['title']} ({d['kind']}, {size} chars extracted)")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    vehicle = db.get_active_vehicle(update.effective_user.id)
    if not vehicle:
        await update.message.reply_text("No active vehicle. Use /vehicles or /addvehicle.")
        return
    videos = db.get_cached_links(vehicle["id"], "video")
    pdfs = db.get_cached_links(vehicle["id"], "pdf")
    if not videos and not pdfs:
        await update.message.reply_text("No cached resources yet — ask me a repair question first.")
        return
    lines = [f"🔗 Cached resources for *{vehicle['name']}*:"]
    if videos:
        lines.append("\n🎬 *Videos*")
        lines += [f"- [{v['title'] or v['url']}]({v['url']})" for v in videos]
    if pdfs:
        lines.append("\n📄 *PDF manuals*")
        lines += [f"- [{p['title'] or p['url']}]({p['url']})" for p in pdfs]
    await _send_long(
        update, "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
    )


# --------------------------------------------------------------------------- #
# License-plate lookup (data.gov.il registries)
# --------------------------------------------------------------------------- #
async def cmd_plate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    raw = " ".join(context.args) if context.args else ""
    plate = vehicle_lookup.normalize_plate(raw)
    if not plate:
        await update.message.reply_text(
            "שימוש: /plate <מספר רכב>\nלמשל: /plate 12345678"
        )
        return

    await update.effective_chat.send_action(ChatAction.TYPING)
    placeholder = await update.message.reply_text(f"🔎 בודק את מספר רכב {plate} במאגרים…")

    try:
        results = await vehicle_lookup.lookup_plate(plate)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Plate lookup failed")
        await placeholder.edit_text(f"⚠️ שגיאה בחיפוש: {exc}")
        return

    lines = [f"🚗 *מספר רכב {plate}*"]
    found_any = False
    found_record = None
    for source, res in results.items():
        label = vehicle_lookup.SOURCE_LABELS.get(source, source)
        if res.get("error"):
            lines.append(f"\n❓ {label}: שגיאה ({res['error']})")
        elif res["found"]:
            found_any = True
            found_record = res["record"]
            lines.append(f"\n✅ נמצא ב{label}:")
            lines.append(vehicle_lookup.format_record(res["record"]))
        else:
            lines.append(f"\n❌ לא נמצא ב{label}")

    keyboard = None
    if found_any and found_record is not None:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("➕ הוסף לצי", callback_data=f"addplate:{plate}")]]
        )

    await placeholder.delete()
    await _send_long(
        update, "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
        reply_markup=keyboard,
    )


async def on_addplate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not _authorized(user_id):
        return
    plate = query.data.split(":", 1)[1]

    existing = db.find_vehicle_by_plate(user_id, plate)
    if existing:
        db.set_active_vehicle(user_id, existing["id"])
        await query.edit_message_text(
            f"✅ הרכב {plate} כבר בצי (*{existing['name']}*) — הוגדר כפעיל.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    results = await vehicle_lookup.lookup_plate(plate)
    record = next(
        (r["record"] for r in results.values() if r.get("found") and r.get("record")), None
    )
    if not record:
        await query.edit_message_text("⚠️ לא הצלחתי לאחזר שוב את פרטי הרכב. נסה /plate שוב.")
        return

    fields = vehicle_lookup.record_to_vehicle_fields(record)
    vehicle_id = db.add_vehicle(user_id, **fields)
    db.set_active_vehicle(user_id, vehicle_id)
    await query.edit_message_text(
        f"✅ נוסף לצי: *{fields['name']}* (id `{vehicle_id}`) והוגדר כפעיל.\n"
        "אפשר לשלוח PDF של ספר שירות, או פשוט לשאול שאלת תחזוקה.",
        parse_mode=ParseMode.MARKDOWN,
    )


# --------------------------------------------------------------------------- #
# PDF document upload -> FSM doc
# --------------------------------------------------------------------------- #
def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        parts = [(page.extract_text() or "") for page in reader.pages]
        return "\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001 — best-effort extraction
        logger.warning("PDF text extraction failed for %s: %s", path, exc)
        return ""


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    doc = update.message.document
    if not doc or (doc.mime_type or "") != "application/pdf":
        await update.message.reply_text("Please send a PDF file to store as an FSM document.")
        return

    user_id = update.effective_user.id
    vehicle = db.get_active_vehicle(user_id)
    if not vehicle:
        await update.message.reply_text("Select a vehicle first (/vehicles or /addvehicle).")
        return

    await update.effective_chat.send_action(ChatAction.UPLOAD_DOCUMENT)
    file = await doc.get_file()
    safe_name = doc.file_name or f"{doc.file_unique_id}.pdf"
    dest = config.FSM_DIR / f"{vehicle['id']}_{doc.file_unique_id}_{safe_name}"
    await file.download_to_drive(str(dest))

    text = _extract_pdf_text(dest)
    db.add_fsm_doc(
        vehicle["id"], title=safe_name, kind="pdf",
        local_path=str(dest), content_text=text,
    )
    await update.message.reply_text(
        f"📚 Stored *{safe_name}* as an FSM document for *{vehicle['name']}* "
        f"({len(text)} chars extracted).",
        parse_mode=ParseMode.MARKDOWN,
    )


# --------------------------------------------------------------------------- #
# Free-text message -> agent
# --------------------------------------------------------------------------- #
async def _send_agent_answer(update: Update, user_id: int, text: str) -> None:
    """Run the agent for one question and send the answer + link blocks.

    Shared by the command-path message handler and the guided conversation.
    """
    await update.effective_chat.send_action(ChatAction.TYPING)
    placeholder = await update.message.reply_text("🔧 עובד על זה — מחפש מדריכים וסרטונים…")

    try:
        result = await agent.handle_message(user_id, text)
    except ClaudeError as exc:
        await placeholder.edit_text(f"⚠️ {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error handling message")
        await placeholder.edit_text(f"⚠️ Unexpected error: {exc}")
        return

    await placeholder.delete()
    await _send_long(update, result.answer)

    extras: list[str] = []
    if result.videos:
        extras.append("🎬 *Videos*")
        extras += [f"- [{v.get('title') or v['url']}]({v['url']})" for v in result.videos]
    if result.pdfs:
        extras.append("\n📄 *PDF manuals*")
        extras += [f"- [{p.get('title') or p['url']}]({p['url']})" for p in result.pdfs]
    if extras:
        if result.new_links:
            extras.append(f"\n_Cached {result.new_links} new link(s) for next time._")
        await _send_long(
            update, "\n".join(extras),
            parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
        )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _guard(update):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    await _send_agent_answer(update, update.effective_user.id, text)


# --------------------------------------------------------------------------- #
# Guided conversation: /start -> ask plate -> details -> ask question -> answer
# --------------------------------------------------------------------------- #
ASK_PLATE, ASK_QUESTION = range(2)


def _looks_like_plate(text: str) -> bool:
    """A bare license plate: mostly digits, 6–8 of them, no spaces/words."""
    stripped = text.replace("-", "").replace(" ", "")
    return stripped.isdigit() and 5 <= len(stripped) <= 8


async def start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _guard(update):
        return ConversationHandler.END
    await update.message.reply_text(
        "🚗 שלום! אני סוכן התחזוקה של הצי שלך.\n\n"
        "שלח לי *מספר רכב* (לוחית רישוי) ואבדוק אותו במאגרי משרד התחבורה.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ASK_PLATE


async def _lookup_and_show(update: Update, user_id: int, raw: str) -> int:
    """Look up a plate, show details, activate the vehicle, ask the question.

    Returns the next conversation state.
    """
    plate = vehicle_lookup.normalize_plate(raw)
    if not plate:
        await update.message.reply_text("לא זיהיתי מספר רכב. שלח ספרות בלבד, למשל 12345678.")
        return ASK_PLATE

    await update.effective_chat.send_action(ChatAction.TYPING)
    placeholder = await update.message.reply_text(f"🔎 בודק את מספר רכב {plate} במאגרים…")

    try:
        results = await vehicle_lookup.lookup_plate(plate)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Plate lookup failed")
        await placeholder.edit_text(f"⚠️ שגיאה בחיפוש: {exc}\nנסה מספר רכב נוסף.")
        return ASK_PLATE

    lines = [f"🚗 *מספר רכב {plate}*"]
    found_record = None
    for source, res in results.items():
        label = vehicle_lookup.SOURCE_LABELS.get(source, source)
        if res.get("error"):
            lines.append(f"\n❓ {label}: שגיאה ({res['error']})")
        elif res["found"]:
            found_record = found_record or res["record"]
            lines.append(f"\n✅ נמצא ב{label}:")
            lines.append(vehicle_lookup.format_record(res["record"]))
        else:
            lines.append(f"\n❌ לא נמצא ב{label}")

    await placeholder.delete()
    await _send_long(
        update, "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
    )

    if found_record is None:
        await update.message.reply_text(
            "הרכב לא נמצא באף מאגר. שלח מספר רכב אחר, או /cancel ליציאה."
        )
        return ASK_PLATE

    # Save + activate the vehicle so the LLM has context (reuse if already saved).
    existing = db.find_vehicle_by_plate(user_id, plate)
    if existing:
        db.set_active_vehicle(user_id, existing["id"])
        name = existing["name"]
    else:
        fields = vehicle_lookup.record_to_vehicle_fields(found_record)
        vehicle_id = db.add_vehicle(user_id, **fields)
        db.set_active_vehicle(user_id, vehicle_id)
        name = fields["name"]

    await update.message.reply_text(
        f"מצוין — *{name}* מוגדר כעת כרכב הפעיל.\n\n"
        "❓ *מה תרצה לדעת על הרכב?*\n"
        "_למשל: איך מחליפים רפידות בלם? מתי להחליף שרשרת תזמון? כמה שמן מנוע צריך?_",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ASK_QUESTION


async def got_plate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _guard(update):
        return ConversationHandler.END
    return await _lookup_and_show(update, update.effective_user.id, update.message.text or "")


async def got_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _guard(update):
        return ConversationHandler.END
    text = (update.message.text or "").strip()
    if not text:
        return ASK_QUESTION

    # Allow switching cars mid-conversation by sending a new plate.
    if _looks_like_plate(text):
        return await _lookup_and_show(update, update.effective_user.id, text)

    await _send_agent_answer(update, update.effective_user.id, text)
    await update.message.reply_text(
        "אפשר לשאול שאלה נוספת על אותו רכב, או לשלוח מספר רכב חדש כדי להחליף רכב."
    )
    return ASK_QUESTION


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("בוטל. שלח /start כדי להתחיל מחדש.")
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    config.validate()
    config.ensure_dirs()
    db.init_db()

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Guided conversation: /start -> plate -> details -> question -> answer.
    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start_flow)],
        states={
            ASK_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_plate)],
            ASK_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_question)],
        },
        fallbacks=[
            CommandHandler("start", start_flow),
            CommandHandler("cancel", cancel),
        ],
        allow_reentry=True,
    )
    app.add_handler(conversation)

    # Auxiliary commands (still available outside / alongside the conversation).
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("addvehicle", cmd_addvehicle))
    app.add_handler(CommandHandler("vehicles", cmd_vehicles))
    app.add_handler(CommandHandler("select", cmd_select))
    app.add_handler(CommandHandler("info", cmd_info))
    app.add_handler(CommandHandler("fsm", cmd_fsm))
    app.add_handler(CommandHandler("links", cmd_links))
    app.add_handler(CommandHandler("plate", cmd_plate))
    app.add_handler(CallbackQueryHandler(on_select_callback, pattern=r"^select:"))
    app.add_handler(CallbackQueryHandler(on_addplate_callback, pattern=r"^addplate:"))
    app.add_handler(MessageHandler(filters.Document.PDF, on_document))

    # Text sent outside any conversation: nudge the user to start.
    async def _nudge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await _guard(update):
            await update.message.reply_text("שלח /start כדי להתחיל 🙂")

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _nudge))

    logger.info("AllDataCars bot starting (polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
