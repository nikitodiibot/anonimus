import logging
import sqlite3
import os
import sys
import time
from datetime import datetime
from urllib.parse import quote
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# db (новая красивая версия)
from db import Database
db = Database()

# ----------------- ENV -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SPECIAL_USER_ID = int(os.getenv("SPECIAL_USER_ID", "0"))

# ----------------- Logging to file -----------------
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)
log_filename = LOGS_DIR / f"errors_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# --------------- Config ---------------
MAX_MSG_LENGTH = 2000
RATE_LIMIT_SECONDS = 3


# ----------------- UI helpers -----------------
def user_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 Профиль", callback_data="profile")
        ],
        [
            InlineKeyboardButton("📬 Моя ссылка", callback_data="my_link")
        ],
        [
            InlineKeyboardButton("📥 Входящие", callback_data="inbox")
        ],
        [
            InlineKeyboardButton("📮 Поддержка", callback_data="support")
        ]
    ])

def admin_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 Пользователи", callback_data="admin_users"),
            InlineKeyboardButton("✉ Сообщения", callback_data="admin_messages"),
        ],
        [
            InlineKeyboardButton("🔍 Поиск по ID", callback_data="admin_lookup"),
            InlineKeyboardButton("⛔ Бан", callback_data="admin_ban"),
        ],
        [
            InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton("📂 Экспорт БД", callback_data="admin_export"),
        ],
        [
            InlineKeyboardButton("🔄 Перезагрузка бота", callback_data="admin_restart")
        ]
    ])


def share_button(user_id: int, bot_username: str):
    link = f"https://t.me/{bot_username}?start={user_id}"
    url = f"https://t.me/share/url?url={quote(link)}&text={quote('Напиши мне анонимно: ' + link)}"
    return InlineKeyboardMarkup([[InlineKeyboardButton("📢 Поделиться ссылкой", url=url)]])


# ----------------- Helpers -----------------
def is_rate_limited(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    last = context.user_data.get("last_sent_ts")
    now_ts = time.time()
    if last and (now_ts - last) < RATE_LIMIT_SECONDS:
        return True

    db_last = db.get_last_sent(user_id)
    if db_last:
        try:
            dt = datetime.fromisoformat(db_last)
            if (now_ts - dt.timestamp()) < RATE_LIMIT_SECONDS:
                return True
        except Exception:
            pass
    return False


def update_rate_limit(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["last_sent_ts"] = time.time()
    try:
        db.update_last_sent(user_id)
    except Exception:
        pass


# ----------------- /start -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, user.username or "", user.first_name or "")

    # deep-link handling
    args = context.args if hasattr(context, "args") else []
    if args:
        try:
            target = int(args[0])
            if target != user.id:
                context.user_data["target_id"] = target
                await update.message.reply_text(
                    "✉ Вы перешли по персональной ссылке.\nНапишите текст анонимного сообщения — оно будет отправлено сразу.",
                    reply_markup=share_button(user.id, context.bot.username)
                )
                return
        except Exception:
            pass

    await update.message.reply_text("👋 Привет! Выбери действие:", reply_markup=user_menu())


# ----------------- Admin command -----------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return
    await update.message.reply_text("🔐 Админ-панель:", reply_markup=admin_menu())


# ----------------- Callback -----------------
async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data
    user = query.from_user
# ---- админ отвечает пользователю ----
    if data.startswith("support_reply_"):
       target = int(data.split("_")[2])
       context.user_data["reply_support"] = target
       await query.message.reply_text(f"✏ Напишите ответ пользователю {target}:")
       return
# ====== Поддержка ======
    if data == "support":
      context.user_data["support_waiting"] = True
      await query.message.reply_text(
        "📮 Напишите ваш вопрос, я передам его администратору."
    )
      return

    # my link
    if data == "my_link":
        link = f"https://t.me/{context.bot.username}?start={user.id}"
        await query.message.reply_text(f"✉ Ваша персональная ссылка:\n{link}", reply_markup=share_button(user.id, context.bot.username))
        return

    # inbox (show latest with pagination)
    if data == "inbox":
        rows = db.get_inbox(user.id, limit=10, offset=0)
        if not rows:
            await query.message.reply_text("📭 У вас пока нет входящих.", reply_markup=user_menu())
            return

        await query.message.reply_text("📥 Ваши входящие (последние):")
        for r in rows:
            # id, from_user, text, media, created_at, delivered, reply_to
            msg_id, from_user, text, media, created_at, delivered, reply_to = r
            preview = (text or "")[:400]
            await query.message.reply_text(
                f"#{msg_id} — {preview}",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("Открыть", callback_data=f"open_{msg_id}"),
                        InlineKeyboardButton("Ответить", callback_data=f"reply_{msg_id}")
                    ]
                ])
            )
        # show "load more" if more messages exist
        total = db.get_messages_count_for_user(user.id)
        if total > 10:
            await query.message.reply_text("Показать предыдущие:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ещё 10", callback_data="inbox_more_10_10")]]))
        return

    # load more inbox (simple offset pattern: inbox_more_<limit>_<offset>)
    if data and data.startswith("inbox_more_"):
        try:
            parts = data.split("_")
            limit = int(parts[2])
            offset = int(parts[3])
        except:
            limit, offset = 10, 10
        rows = db.get_inbox(user.id, limit=limit, offset=offset)
        if not rows:
            await query.message.reply_text("Больше нет сообщений.", reply_markup=user_menu())
            return
        for r in rows:
            msg_id, from_user, text, media, created_at, delivered, reply_to = r
            preview = (text or "")[:400]
            await query.message.reply_text(
                f"#{msg_id} — {preview}",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("Открыть", callback_data=f"open_{msg_id}"),
                        InlineKeyboardButton("Ответить", callback_data=f"reply_{msg_id}")
                    ]
                ])
            )
        # next offset
        await query.message.reply_text("Если нужно — нажмите ещё.", reply_markup=user_menu())
        return

    # open full message
    if data and data.startswith("open_"):
        try:
            msg_id = int(data.split("_", 1)[1])
        except:
            await query.message.reply_text("Неверный ID.")
            return
        # find message in DB (simple query)
        # we can reuse get_inbox or direct query
        try:
            conv = db.get_conversation(user.id, user.id, limit=200)  # harmless: just to satisfy import; we'll fetch directly
        except Exception:
            pass
        # fetch message by id
        conn = sqlite3.connect(db.DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, from_user, to_user, text, media, created_at, delivered, reply_to FROM messages WHERE id=?", (msg_id,))
        mm = cur.fetchone()
        conn.close()
        if not mm:
            await query.message.reply_text("Сообщение не найдено.")
            return
        mid, from_user, to_user, text, media, created_at, delivered, reply_to = mm
        header = f"📨 Сообщение #{mid} от {from_user} ({created_at}):"
        if reply_to:
            header = f"📨 Ответ на #{reply_to} — от {from_user} ({created_at}):"
        await query.message.reply_text(header + "\n\n" + (text or ""), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ответить", callback_data=f"reply_{mid}")]]))
        return

    # reply callback: prepare to reply to a specific message id
    if data and data.startswith("reply_"):
        # pattern reply_<msg_id>
        try:
            msg_id = int(data.split("_", 1)[1])
        except:
            await query.message.reply_text("Неверный ID для ответа.")
            return
        # find the original message to know recipient
        conn = sqlite3.connect(db.DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT id, from_user, to_user FROM messages WHERE id=?", (msg_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            await query.message.reply_text("Исходное сообщение не найдено.")
            return
        mid, from_user, to_user = row
        # we want current user to reply to the owner of the message;
        # if current user is the recipient, reply_to_target = from_user
        # if current user is the sender, reply_to_target = to_user
        current = user.id
        if current == to_user:
            target_user = from_user
        else:
            target_user = to_user
        context.user_data["reply_to_msg"] = mid
        context.user_data["reply_to_target"] = target_user
        await query.message.reply_text("✏️ Напишите ваш ответ — он будет привязан к сообщению.", reply_markup=user_menu())
        return

    # ---------- admin blocks ----------
    if data and data.startswith("admin_"):
        if user.id != ADMIN_ID:
            await query.message.reply_text("❌ Нет доступа.")
            return

        cmd = data.split("_", 1)[1]

        # connect once
        conn = sqlite3.connect(db.DB_PATH)
        cur = conn.cursor()

        # users
        if cmd == "users":
            cur.execute("SELECT user_id, username, first_name, joined FROM users ORDER BY joined DESC LIMIT 200")
            rows = cur.fetchall()
            conn.close()
            txt = "👥 Пользователи:\n\n" + "\n".join(f"{u[0]} | @{u[1] or '-'} | {u[2] or '-'} | {u[3] or '-'}" for u in rows)
            await query.message.reply_text(txt)
            return

        # messages
        if cmd == "messages":
            cur.execute("SELECT id, from_user, to_user, text, created_at FROM messages ORDER BY id DESC LIMIT 40")
            rows = cur.fetchall()
            conn.close()
            txt = "✉ Последние сообщения:\n\n" + "\n".join(f"#{r[0]}: {r[1]} -> {r[2]} — {(r[3] or '')[:40]} ({r[4]})" for r in rows)
            await query.message.reply_text(txt)
            return

        # lookup by id (start interactive)
        if cmd == "lookup":
            context.user_data["admin_waiting_lookup"] = True
            await query.message.reply_text("Введите user_id для просмотра сообщений:", reply_markup=admin_menu())
            conn.close()
            return

        # ban interactive
        if cmd == "ban":
            context.user_data["admin_waiting_ban"] = True
            await query.message.reply_text("Введите user_id для бана:", reply_markup=admin_menu())
            conn.close()
            return

        # broadcast interactive
        if cmd == "broadcast":
            context.user_data["admin_waiting_broadcast"] = True
            await query.message.reply_text("Введите текст рассылки (админ):", reply_markup=admin_menu())
            conn.close()
            return

        # stats
        if cmd == "stats":
            cur.execute("SELECT COUNT(*) FROM users")
            users_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM messages")
            msg_count = cur.fetchone()[0]
            conn.close()
            await query.message.reply_text(f"📊 Статистика: пользователей {users_count}, сообщений {msg_count}")
            return

        # export
        if cmd == "export":
            await query.message.reply_text("📂 Экспорт БД...")
            try:
                await context.bot.send_document(chat_id=user.id, document=open(db.DB_PATH, "rb"))
            except Exception as e:
                logger.exception("Export error: %s", e)
            conn.close()
            return

        # restart
        if cmd == "restart":
            await query.message.reply_text("🔄 Перезапуск бота...")
            conn.close()
            os.execv(sys.executable, [sys.executable] + sys.argv)
            return

    # profile and info
    if data == "profile":
        await query.message.reply_text(f"👤 Профиль:\nID: {user.id}\nUsername: @{user.username}\nИмя: {user.first_name}")
        return

    if data == "info":
        await query.message.reply_text("ℹ️ Бот принимает только текстовые анонимные сообщения.")
        return

    # fallback
    await query.message.reply_text("❓ Неизвестная команда.", reply_markup=user_menu())


# ----------------- TEXT handler -----------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
        # --- пользователь пишет в поддержку ---
    if context.user_data.get("support_waiting"):
        context.user_data.pop("support_waiting")

        admin_id = ADMIN_ID

        await context.bot.send_message(
            admin_id,
            f"🆘 Новое обращение в поддержку\n"
            f"От пользователя: {user.id}\n\n"
            f"Текст:\n{text}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Ответить", callback_data=f"support_reply_{user.id}")]
            ])
        )

        await update.message.reply_text("✔ Ваше сообщение отправлено в поддержку!")
        return


    # --- админ отправляет ответ ---
    if context.user_data.get("reply_support"):
        target = context.user_data.pop("reply_support")

        await context.bot.send_message(
            target,
            f"📮 Ответ поддержки:\n\n{text}"
        )

        await update.message.reply_text("✔ Ответ отправлен пользователю.")
        return

    # ensure user exists
    db.ensure_user(user.id, user.username or "", user.first_name or "")

    # check banned
    try:
        if db.is_user_banned(user.id):
            await update.message.reply_text("⛔ Вы заблокированы и не можете отправлять сообщения.")
            return
    except Exception:
        logger.exception("Error checking banned status")

    # admin interactive: lookup
    if context.user_data.get("admin_waiting_lookup") and user.id == ADMIN_ID and not text.startswith("/"):
        context.user_data.pop("admin_waiting_lookup")
        try:
            uid = int(text.strip())
        except:
            await update.message.reply_text("Некорректный ID.", reply_markup=admin_menu())
            return
        rows = db.get_messages_by_user(uid, limit=200)
        if not rows:
            await update.message.reply_text("Нет сообщений для этого ID.", reply_markup=admin_menu())
            return
        out = "Последние сообщения:\n\n" + "\n".join(f"#{r[0]} {r[1]} → {r[2]} | {(r[3] or '')[:80]} | {r[4]}" for r in rows)
        for chunk in [out[i:i+4000] for i in range(0, len(out), 4000)]:
            await update.message.reply_text(chunk)
        await update.message.reply_text("Готово.", reply_markup=admin_menu())
        return

    # admin interactive: ban
    if context.user_data.get("admin_waiting_ban") and user.id == ADMIN_ID and not text.startswith("/"):
        context.user_data.pop("admin_waiting_ban")
        try:
            uid = int(text.strip())
        except:
            await update.message.reply_text("Некорректный ID.", reply_markup=admin_menu())
            return
        db.set_user_banned(uid, 1)
        await update.message.reply_text(f"Пользователь {uid} забанен.", reply_markup=admin_menu())
        return

    # admin interactive: broadcast
    if context.user_data.get("admin_waiting_broadcast") and user.id == ADMIN_ID and not text.startswith("/"):
        context.user_data.pop("admin_waiting_broadcast")
        broadcast_text = text.strip()
        conn = sqlite3.connect(db.DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE banned IS NULL OR banned=0")
        users = [u[0] for u in cur.fetchall()]
        conn.close()
        count = 0
        for uid in users:
            try:
                await context.bot.send_message(uid, f"📢 Администрация:\n\n{broadcast_text}")
                count += 1
                time.sleep(0.03)
            except Exception:
                pass
        await update.message.reply_text(f"✔ Рассылка завершена. Уведомлено: {count}.", reply_markup=admin_menu())
        return

    # special greeting (only on ordinary message from special user)
    if user.id == SPECIAL_USER_ID and not context.user_data.get("special_greeted"):
        context.user_data["special_greeted"] = True
        await update.message.reply_text("🌟 здравствуй,Папа! Я так рад снова тебя видеть 💖\n\nВыбери действие из меню:", reply_markup=user_menu())
        return

    # length check
    if len(text) > MAX_MSG_LENGTH:
        await update.message.reply_text(f"❗ Сообщение слишком длинное (максимум {MAX_MSG_LENGTH}).", reply_markup=user_menu())
        return

    # reply flow (user replies to specific message)
    if context.user_data.get("reply_to_msg") and context.user_data.get("reply_to_target"):
        reply_mid = context.user_data.pop("reply_to_msg")
        target = context.user_data.pop("reply_to_target")

        if target == user.id:
            await update.message.reply_text("Нельзя отправлять сообщение самому себе.", reply_markup=user_menu())
            return

        if is_rate_limited(user.id, context):
            await update.message.reply_text("⏳ Подождите пару секунд перед следующим сообщением.", reply_markup=user_menu())
            return

        # save with reply_to = reply_mid
        msg_id = db.save_message(
    from_user=user.id,
    to_user=target,
    text=text,
    media=None,
    delivered=1,
    reply_to=reply_mid
)
        try:
            await context.bot.send_message(target, f"📨 Анонимный ответ (на #{reply_mid}):\n\n{text}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ответить", callback_data=f"reply_{msg_id}")]]))
            update_rate_limit(user.id, context)
            await update.message.reply_text("✔ Ответ отправлен.", reply_markup=user_menu())
        except Exception as e:
            logger.exception("Failed to send anonymous reply: %s", e)
            # optionally mark delivered=0 by updating DB, but for now we keep delivered=1 or could mark 0
            await update.message.reply_text("⚠ Ответ пока не доставлен. Мы попробуем доставить его позже.", reply_markup=user_menu())
        return

    # deep-link flow: target_id
    if context.user_data.get("target_id"):
        target = context.user_data.pop("target_id")
        if target == user.id:
            await update.message.reply_text("Нельзя отправлять анонимные сообщения самому себе!", reply_markup=user_menu())
            return

        if is_rate_limited(user.id, context):
            await update.message.reply_text("⏳ Подождите пару секунд.", reply_markup=user_menu())
            return

        msg_id = db.save_message(user.id, target, text)
        try:
            await context.bot.send_message(target, f"📨 Анонимное сообщение:\n\n{text}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ответить", callback_data=f"reply_{msg_id}")]]))
            update_rate_limit(user.id, context)
            await update.message.reply_text("✔ Сообщение отправлено!", reply_markup=share_button(user.id, context.bot.username))
        except Exception as e:
            logger.exception("Failed to send anonymous message: %s", e)
            await update.message.reply_text("⚠ Ваше сообщение пока не доставлено.", reply_markup=user_menu())
        return

    # default: show menu
    await update.message.reply_text("📌 Выберите действие:", reply_markup=user_menu())


# ----------------- error handler -----------------
async def error_handler(update, context):
    logger.exception("Update caused error", exc_info=context.error)


# ----------------- MAIN -----------------
def main():
    # prepare DB and logging
    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    async def _post_init(app):
        await app.bot.set_my_commands([
            BotCommand("start", "Запустить бота"),
            BotCommand("menu", "Открыть меню"),
            BotCommand("admin", "Открыть админ-панель")
        ])
    app.post_init = _post_init

    # handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.add_error_handler(error_handler)

    logger.info("Bot started!")
    app.run_polling()


if __name__ == "__main__":
    main()

