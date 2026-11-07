"""Cliente HTTP de teste que lida com o token de entrada e o CSRF do ATENA."""
import re

BASE = "http://127.0.0.1:5000"


class Cliente:
    def __init__(self, app, token="tok"):
        app.config["PROPAGATE_EXCEPTIONS"] = True
        self.c = app.test_client()
        self.c.get(f"/entrar?t={token}", base_url=BASE)
        with self.c.session_transaction(base_url=BASE) as s:
            self.csrf = s["csrf"]

    def get(self, url):
        r = self.c.get(url, base_url=BASE)
        assert r.status_code in (200, 302), (url, r.status_code)
        r.text = r.get_data().decode('utf-8', 'replace')
        return r

    def post(self, url, _espera_erro=False, **dados):
        dados["_csrf"] = self.csrf
        r = self.c.post(url, data=dados, base_url=BASE, content_type="multipart/form-data")
        assert r.status_code in (200, 302), (url, r.status_code, r.get_data()[:500])
        r.text = r.get_data().decode('utf-8', 'replace')
        if r.status_code == 302:
            with self.c.session_transaction(base_url=BASE) as s:
                erros = [m for cat, m in s.get("_flashes", []) if cat == "erro"]
            assert bool(erros) == _espera_erro, (url, erros)
        return r

    def api(self, url, **dados):
        r = self.c.post(url, json=dados, base_url=BASE, headers={"X-CSRF": self.csrf})
        assert r.status_code == 200, (url, r.get_json())
        return r.get_json()

    def api_get(self, url):
        r = self.c.get(url, base_url=BASE)
        assert r.status_code == 200
        return r.get_json()
