import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand, CommandError

from assist_platform.services import handle_telegram_update


class Command(BaseCommand):
    help = "Poll Telegram Bot API updates to complete caregiver account linking."

    def handle(self, *args, **options):
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise CommandError("Set TELEGRAM_BOT_TOKEN in backend/.env before starting the Telegram poller.")

        configured_username = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
        if not configured_username:
            raise CommandError("Set TELEGRAM_BOT_USERNAME in backend/.env before starting the Telegram poller.")

        try:
            bot = self._telegram_request(token, "getMe")
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise CommandError(f"Could not verify the Telegram bot: {self._safe_error(exc)}") from None
        if not bot.get("ok"):
            raise CommandError(f"Telegram rejected the bot token: {bot.get('description', 'unknown API error')}")
        actual_username = str(bot.get("result", {}).get("username", ""))
        if actual_username.casefold() != configured_username.casefold():
            raise CommandError(
                f"TELEGRAM_BOT_USERNAME is @{configured_username}, but this token belongs to @{actual_username}. "
                "Update backend/.env so the bot username and token belong together."
            )

        offset = 0
        self.stdout.write(f"Telegram caregiver-link poller connected to @{actual_username}. Press Ctrl+C to stop.")
        while True:
            query = urlencode({"timeout": 25, "offset": offset, "allowed_updates": json.dumps(["message"])})
            try:
                result = self._telegram_request(token, "getUpdates", query=query, timeout=35)
                if not result.get("ok"):
                    description = str(result.get("description", "unknown Telegram API error"))
                    if result.get("error_code") == 409:
                        self.stderr.write(
                            "Telegram polling conflict (409). Stop every other poll_telegram process for this bot; "
                            "Telegram permits only one getUpdates poller at a time."
                        )
                    elif result.get("error_code") == 401:
                        self.stderr.write("Telegram rejected the bot token (401). Replace it in backend/.env and restart this poller.")
                    else:
                        self.stderr.write(f"Telegram polling rejected: {description}")
                    time.sleep(3)
                    continue
                for update in result.get("result", []):
                    try:
                        offset = max(offset, handle_telegram_update(update))
                    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                        self.stderr.write(f"Could not process Telegram update {update.get('update_id')}: {self._safe_error(exc)}")
                        time.sleep(1)
                        break
            except HTTPError as exc:
                self.stderr.write(f"Telegram polling HTTP error: {self._http_error_description(exc)}")
                time.sleep(3)
            except (URLError, TimeoutError, ValueError) as exc:
                detail = self._safe_error(exc)
                if "HTTP 409" in detail or "terminated by other getUpdates request" in detail:
                    self.stderr.write(
                        "Telegram polling conflict (409). Stop every other poll_telegram process for this bot; "
                        "Telegram permits only one getUpdates poller at a time."
                    )
                elif "HTTP 401" in detail:
                    self.stderr.write("Telegram rejected the bot token (401). Replace it in backend/.env and restart this poller.")
                else:
                    self.stderr.write(f"Telegram polling failed: {detail}; retrying in 3 seconds.")
                time.sleep(3)

    @staticmethod
    def _telegram_request(token, method, query="", timeout=10):
        request = Request(f"https://api.telegram.org/bot{token}/{method}{'?' + query if query else ''}")
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except HTTPError as exc:
            raise ValueError(f"Telegram API HTTP {exc.code}: {Command._http_error_description(exc)}") from None

    @staticmethod
    def _http_error_description(error):
        try:
            payload = json.loads(error.read().decode(errors="replace"))
            description = payload.get("description")
            if description:
                return str(description)
        except (ValueError, AttributeError):
            pass
        return f"HTTP {error.code}"

    @staticmethod
    def _safe_error(error):
        if isinstance(error, URLError):
            reason = getattr(error, "reason", None)
            return f"network error ({type(reason).__name__})" if reason else "network error"
        if isinstance(error, ValueError):
            return str(error)
        return f"{type(error).__name__}"
