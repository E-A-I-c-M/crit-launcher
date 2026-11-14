"""Change an Ely.by skin through the website's own browser session.

Ely.by's game access token is not a website session and cannot authorize skin
writes. Keep it out of these requests; the user signs in to the Ely.by site in
its own webview when needed.
"""

import base64
import json
import threading
import time
from urllib.parse import urlparse


ELY_SKINS_PAGE = "https://ely.by/"
ELY_LOGIN_PAGE = "https://ely.by/authorization/login"
_LOGIN_TIMEOUT = 10 * 60
_POLL_INTERVAL = 0.5
_SITE_LOCK = threading.Lock()


# Ely.by reports `error_login` for this non-existent skin ID when its website
# session is missing. It is only a session check; ID 0 is never a real skin.
_SESSION_PROBE = r"""(async () => {
  const response = await fetch('/skins/remove/0', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'X-Requested-With': 'XMLHttpRequest',
      'Accept': 'application/json',
    },
  });
  let json = null;
  try { json = await response.json(); } catch (_) {}
  const redirectedToLogin = response.redirected &&
    /\/authorization(?:\/|$)/.test(new URL(response.url).pathname);
  return {
    status: (json && json.error === 'error_login') || redirectedToLogin ? 'signed_out' : 'done',
  };
})()"""


_REQUEST_HELPERS = r"""
const call = async (path, init = {}) => {
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      'X-Requested-With': 'XMLHttpRequest',
      ...(init.headers || {}),
    },
  });
  const text = await response.text();
  let json = null;
  try { json = JSON.parse(text); } catch (_) {}
  return { response, json };
};
const problem = ({ response, json }) => {
  const redirectedToLogin = response.redirected &&
    /\/authorization(?:\/|$)/.test(new URL(response.url).pathname);
  if ((json && json.error === 'error_login') || redirectedToLogin) {
    return { status: 'signed_out' };
  }
  if (!response.ok || (json && json.error)) {
    const message = json && (json.text || json.error);
    return {
      status: 'failed',
      message: message
        ? String(message).replace(/<[^>]*>/g, '').slice(0, 300)
        : `HTTP ${response.status}`,
    };
  }
  return null;
};
const form = (fields) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
  body: new URLSearchParams(fields).toString(),
});
"""


def _upload_script(png: bytes):
    encoded = base64.b64encode(png).decode("ascii")
    return f"""(async () => {{
{_REQUEST_HELPERS}
  const bytes = Uint8Array.from(atob({json.dumps(encoded)}), (char) => char.charCodeAt(0));
  const body = new FormData();
  body.append('file', new File([bytes], 'skin.png', {{ type: 'image/png' }}));
  const uploaded = await call('/skins/upload', {{ method: 'POST', body }});
  const failed = problem(uploaded);
  if (failed) return failed;
  const rawUrl = uploaded.json && uploaded.json.url;
  if (!rawUrl) return {{ status: 'failed', message: 'Ely.by did not identify the uploaded skin' }};
  const address = new URL(String(rawUrl), location.origin);
  if (address.origin !== location.origin) {{
    return {{ status: 'failed', message: 'Ely.by returned an unexpected skin address' }};
  }}
  const match = address.pathname.match(/^\/skins\/s(\d+)(?:\/)?$/);
  const skinId = match ? Number(match[1]) : 0;
  if (!Number.isSafeInteger(skinId) || skinId <= 0) {{
    return {{ status: 'failed', message: 'Ely.by did not identify the uploaded skin' }};
  }}
  return {{ status: 'done', skin_id: skinId }};
}})()"""


def _model_script(skin_id: int, variant: str):
    slim = "1" if variant == "slim" else "0"
    return f"""(async () => {{
{_REQUEST_HELPERS}
  const id = {int(skin_id)};
  const pageResponse = await fetch(`/skins/s${{id}}/edit`, {{
    credentials: 'same-origin',
    headers: {{ 'X-Requested-With': 'XMLHttpRequest' }},
  }});
  if (pageResponse.redirected &&
      /\/authorization(?:\/|$)/.test(new URL(pageResponse.url).pathname)) {{
    return {{ status: 'signed_out' }};
  }}
  if (!pageResponse.ok) {{
    return {{ status: 'failed', message: `HTTP ${{pageResponse.status}}` }};
  }}
  const page = await pageResponse.text();
  const found = page.match(/alight\.service\.skin\s*=\s*(\{{[\s\S]*?\}});/);
  if (!found) {{
    if (page.includes('error_login')) return {{ status: 'signed_out' }};
    return {{ status: 'failed', message: 'Ely.by would not open this skin for editing' }};
  }}
  let skin;
  try {{ skin = JSON.parse(found[1]); }} catch (_) {{
    return {{ status: 'failed', message: 'Ely.by returned unreadable skin details' }};
  }}
  const saved = await call(`/skins/save/${{id}}`, form({{
    name: skin.name || '',
    description: skin.description || '',
    kind: String(skin.kind == null ? 0 : skin.kind),
    color: String(skin.color || '').replace('#', ''),
    tags: Array.isArray(skin.tags) ? skin.tags.join(',') : String(skin.tags || ''),
    isSlim: '{slim}',
  }}));
  return problem(saved) || {{ status: 'done' }};
}})()"""


def _wear_script(skin_id: int):
    return f"""(async () => {{
{_REQUEST_HELPERS}
  const worn = await call('/skins/wear', form({{ skinId: '{int(skin_id)}' }}));
  return problem(worn) || {{ status: 'done' }};
}})()"""


def _website_url(window):
    try:
        return window.get_current_url() or ""
    except Exception:
        return ""


def _secure_ely_host(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.scheme != "https" or port not in (None, 443):
        return None
    return parsed.hostname


def _is_ely_page(url: str) -> bool:
    return _secure_ely_host(url) == "ely.by"


def _evaluate(window, script):
    result = window.evaluate_js(script)
    if not isinstance(result, dict) or result.get("status") not in ("done", "signed_out", "failed"):
        raise RuntimeError("Ely.by returned an unreadable response.")
    return result


def _confirm_account(window, account_name: str) -> bool:
    window.set_title(f"Ely.by — verify {account_name}")
    message = (
        "Crit Launcher will upload and apply this skin to the Ely.by profile signed in "
        "in this window. Continue only if the profile shown on the Ely.by website is "
        f"{account_name!r}. If it is a different account, choose Cancel; this window "
        "will stay open so you can sign out and switch accounts."
    )
    return bool(window.create_confirmation_dialog("Confirm Ely.by account", message))


def _wait_for_login(window, closed, account_name: str):
    window.set_title(f"Sign in to Ely.by as {account_name}")
    window.show()
    window.load_url(ELY_LOGIN_PAGE)
    deadline = time.monotonic() + _LOGIN_TIMEOUT

    while time.monotonic() < deadline:
        if closed.is_set():
            return "cancelled"
        url = _website_url(window)
        if closed.is_set():
            return "cancelled"
        if _is_ely_page(url):
            try:
                result = _evaluate(window, _SESSION_PROBE)
            except Exception:
                result = None
            if result and result.get("status") != "signed_out":
                return "signed_in"
        time.sleep(_POLL_INTERVAL)

    return "timeout"


def _result_error(result, uploaded=False):
    status = result.get("status")
    if status == "cancelled":
        message = result.get("message") or "The Ely.by skin change was cancelled."
    elif status == "signed_out":
        message = "Ely.by still needs you to sign in. No skin change was applied."
    else:
        message = result.get("message") or "Ely.by did not accept the skin change."
    if uploaded:
        message = "The skin was uploaded to Ely.by but not applied. " + message
    return {"success": False, "error": message}


def _run_step(window, closed, account_name: str, script):
    if closed.is_set():
        return {"status": "cancelled", "message": "The Ely.by window was closed."}
    if not _is_ely_page(_website_url(window)):
        return {"status": "failed", "message": "The Ely.by window left the Ely.by website."}

    result = _evaluate(window, script)
    if result.get("status") != "signed_out":
        return result

    login = _wait_for_login(window, closed, account_name)
    if login != "signed_in":
        if login == "timeout":
            return {"status": "failed", "message": "Ely.by sign-in timed out. No skin change was applied."}
        return {"status": "cancelled", "message": "Ely.by sign-in was cancelled. No skin change was applied."}
    if not _confirm_account(window, account_name):
        window.set_title("Ely.by — switch accounts or close this window")
        return {
            "status": "cancelled",
            "message": (
                "The Ely.by skin change was cancelled. The Ely.by window was left open "
                "so you can switch accounts."
            ),
            "keep_window_open": True,
        }

    result = _evaluate(window, script)
    if result.get("status") == "signed_out":
        return {
            "status": "failed",
            "message": "Ely.by still reports that the website session is signed out.",
        }
    return result


def apply_ely_skin(account: dict, png: bytes, variant: str):
    """Upload a validated PNG, set its Ely.by model, then wear it on that account."""
    account_name = str((account or {}).get("name") or "").strip()
    if not account_name:
        return {"success": False, "error": "The Ely.by account has no username."}

    try:
        import webview
    except ImportError:
        return {"success": False, "error": "Ely.by skin changes need the launcher's webview."}

    variant = "slim" if variant == "slim" else "classic"
    with _SITE_LOCK:
        window = None
        closed = threading.Event()
        keep_window_open = False
        try:
            window = webview.create_window(
                f"Ely.by — apply skin to {account_name}",
                ELY_SKINS_PAGE,
                width=900,
                height=700,
                min_size=(560, 480),
                resizable=True,
                background_color="#ffffff",
            )
            if window is None:
                return {"success": False, "error": "Couldn't open Ely.by. No skin change was applied."}
            try:
                window.events.closed += lambda *args: closed.set()
            except Exception:
                pass

            if closed.is_set():
                return {"success": False, "error": "The Ely.by window was closed. No skin change was applied."}
            url = _website_url(window)
            host = _secure_ely_host(url)
            if host == "www.ely.by":
                # Use the canonical origin for same-origin skin requests.
                window.load_url(ELY_SKINS_PAGE)
                url = _website_url(window)
                host = _secure_ely_host(url)
            if host not in ("ely.by", "account.ely.by"):
                return {
                    "success": False,
                    "error": "Couldn't load Ely.by over HTTPS. No skin change was applied.",
                }

            if host == "account.ely.by":
                login = _wait_for_login(window, closed, account_name)
                if login != "signed_in":
                    if login == "timeout":
                        message = "Ely.by sign-in timed out. No skin change was applied."
                    else:
                        message = "Ely.by sign-in was cancelled. No skin change was applied."
                    return {"success": False, "error": message}
            else:
                probe = _evaluate(window, _SESSION_PROBE)
                if probe.get("status") == "signed_out":
                    login = _wait_for_login(window, closed, account_name)
                    if login != "signed_in":
                        if login == "timeout":
                            message = "Ely.by sign-in timed out. No skin change was applied."
                        else:
                            message = "Ely.by sign-in was cancelled. No skin change was applied."
                        return {"success": False, "error": message}

            if not _confirm_account(window, account_name):
                keep_window_open = True
                window.set_title("Ely.by — switch accounts or close this window")
                return {
                    "success": False,
                    "error": (
                        "The Ely.by skin change was cancelled. The Ely.by window was left open "
                        "so you can switch accounts."
                    ),
                }

            uploaded = _run_step(window, closed, account_name, _upload_script(png))
            if uploaded.get("keep_window_open"):
                keep_window_open = True
            if uploaded.get("status") != "done":
                return _result_error(uploaded)
            skin_id = uploaded.get("skin_id")
            if not isinstance(skin_id, int) or isinstance(skin_id, bool) or skin_id <= 0:
                return {"success": False, "error": "The skin was uploaded, but Ely.by did not return a valid skin ID."}

            model = _run_step(window, closed, account_name, _model_script(skin_id, variant))
            if model.get("keep_window_open"):
                keep_window_open = True
            if model.get("status") != "done":
                return _result_error(model, uploaded=True)

            worn = _run_step(window, closed, account_name, _wear_script(skin_id))
            if worn.get("keep_window_open"):
                keep_window_open = True
            if worn.get("status") != "done":
                return _result_error(worn, uploaded=True)

            return {"success": True}
        except Exception as exc:
            return {
                "success": False,
                "error": f"Couldn't change the Ely.by skin: {exc}",
            }
        finally:
            if window is not None and not keep_window_open:
                try:
                    window.destroy()
                except Exception:
                    pass
