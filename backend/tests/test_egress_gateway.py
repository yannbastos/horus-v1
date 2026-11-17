"""HOR-111: gateway único de saída HTTP — deny by default, allowlist explícita, anti-rebinding, redirects, redação e auditoria.
Nenhuma rede real: resolvedor e transporte são substituídos; só bancos temporários."""
import json
import sqlite3

import pytest

from horus_backend import egress
from horus_backend.egress import DEFAULT_RULES, EgressBlocked, EgressGateway, Rule, parse_rules
from horus_backend.migrations import migrate
from horus_backend.storage import Store
from horus_backend.transcriber import is_youtube_url
from horus_backend.transmissions import TransmissionLog

PUBLIC = "93.184.216.34"
SECRET = "sk-" + "a1B2c3D4" * 5


class Net:
    """Resolvedor e transporte falsos; registram o que o gateway tentou fazer."""

    def __init__(self, answers=None, responses=None):
        self.answers = answers or {}
        self.responses = list(responses or [(200, {}, b'{"ok":true}')])
        self.resolved, self.connected = [], []

    def resolver(self, host, port):
        self.resolved.append(host)
        answer = self.answers.get(host, [PUBLIC])
        return answer.pop(0) if answer and isinstance(answer[0], list) else answer

    def transport(self, ip, port, scheme, host, method, target, headers, body, timeout, max_bytes):
        self.connected.append({"ip": ip, "port": port, "scheme": scheme, "host": host, "method": method, "target": target,
                               "headers": dict(headers), "body": body})
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


@pytest.fixture
def log(tmp_path):
    path = tmp_path / "horus.sqlite3"
    migrate(path)
    return TransmissionLog(Store(path))


def gateway(net, log=None, rules=None, **kw):
    return EgressGateway(rules if rules is not None else (Rule("api", "https", "api.example.org", methods=frozenset({"GET", "POST"}), allow_auth=True),
                                                       Rule("plain", "https", "files.example.org")),
                         audit=log, resolver=net.resolver, transport=net.transport, **kw)


def rows(log):
    with log.store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM transmissions ORDER BY seq")]


# --- deny by default -----------------------------------------------------------------------------------------

def test_default_policy_lists_only_the_known_product_destinations():
    allowed = {(r.purpose, r.host) for r in DEFAULT_RULES}
    assert allowed == {("youtube_download", "www.youtube.com"), ("youtube_download", "youtube.com"),
                       ("youtube_download", "m.youtube.com"), ("youtube_download", "music.youtube.com"), ("youtube_download", "youtu.be"), ("radar_youtube", "www.googleapis.com"),
                       ("whisper_model_download", "huggingface.co"), ("whisper_model_download", "cdn-lfs.huggingface.co"),
                       ("whisper_model_download", "cdn-lfs-us-1.huggingface.co"), ("whisper_model_download", "cas-bridge.xethub.hf.co")}


@pytest.mark.parametrize("purpose,url", [
    ("api", "https://evil.example.net/x"), ("api", "https://api.example.org.evil.net/x"), ("api", "https://sub.api.example.org/x"),
    ("outra", "https://api.example.org/x"), ("api", "https://api.example.org:8443/x"),
    ("api", "http://api.example.org/x"),                         # esquema errado
    ("api", "ftp://api.example.org/x"), ("api", "file:///C:/Windows/win.ini"), ("api", "javascript:alert(1)"),
    ("api", "https://user:pass@api.example.org/x"), ("api", "https://api.example.org/x#frag"),
    ("api", "https://api.example.org/a b"), ("api", "https://api.example.org/\x00"), ("api", ""), ("api", "//api.example.org/x"),
    ("api", "https://api.example.org:99999/x"),
])
def test_destination_outside_the_allowlist_is_refused_before_any_io(url, purpose):
    net = Net()
    with pytest.raises(EgressBlocked):
        gateway(net).request(purpose, "GET", url)
    assert net.resolved == [] and net.connected == []


def test_allowed_destination_works_and_is_pinned_to_the_validated_ip():
    net = Net()
    response = gateway(net).request("api", "GET", "https://api.example.org/v1/x?y=1")
    assert response.status == 200 and json.loads(response.body) == {"ok": True}
    assert net.connected[0]["ip"] == PUBLIC and net.connected[0]["host"] == "api.example.org" and net.connected[0]["target"] == "/v1/x?y=1"


def test_method_headers_and_credentials_are_restricted():
    net = Net()
    g = gateway(net)
    with pytest.raises(EgressBlocked) as error:
        g.request("plain", "POST", "https://files.example.org/x", body=b"{}")
    assert error.value.code == "method_not_allowed"
    with pytest.raises(EgressBlocked) as error:
        g.request("plain", "GET", "https://files.example.org/x", headers={"Authorization": "Bearer " + "x" * 30})
    assert error.value.code == "auth_not_allowed"
    for headers in ({"Cookie": "a=b"}, {"Proxy-Authorization": "x"}, {"Host": "evil"}, {"X-A": "a\r\nX-B: b"}, {"bad name": "v"}):
        with pytest.raises(EgressBlocked) as error:
            g.request("api", "GET", "https://api.example.org/x", headers=headers)
        assert error.value.code == "header_not_allowed", headers
    assert net.connected == []
    g.request("api", "POST", "https://api.example.org/x", headers={"Authorization": "Bearer abc"}, body={"a": 1})


# --- DNS rebinding / IP privado -------------------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    ["127.0.0.1"], ["10.0.0.5"], ["192.168.1.10"], ["172.16.0.1"], ["169.254.169.254"], ["100.64.0.1"], ["0.0.0.0"],
    ["::1"], ["fe80::1"], ["fc00::1"], ["::ffff:127.0.0.1"], ["::ffff:10.0.0.1"], ["224.0.0.1"],
    [PUBLIC, "10.0.0.5"],                      # resposta mista: basta um endereço interno
])
def test_hostname_resolving_to_a_private_address_is_refused(answer):
    net = Net(answers={"api.example.org": answer})
    with pytest.raises(EgressBlocked) as error:
        gateway(net).request("api", "GET", "https://api.example.org/x")
    assert error.value.code == "private_address" and net.connected == []


def test_dns_is_resolved_once_and_the_connection_uses_that_answer():
    """Rebinding: um segundo lookup devolveria IP interno; o gateway nunca faz o segundo."""
    net = Net(answers={"api.example.org": [[PUBLIC], ["127.0.0.1"]]})
    gateway(net).request("api", "GET", "https://api.example.org/x")
    assert net.resolved == ["api.example.org"] and net.connected[0]["ip"] == PUBLIC


@pytest.mark.parametrize("host", ["127.0.0.1", "10.1.2.3", "[::1]", "169.254.169.254", "localhost"])
def test_private_ip_literals_and_localhost_are_refused_even_if_listed(host):
    rule = Rule("x", "https", host.strip("[]"))
    net = Net()
    with pytest.raises(EgressBlocked):
        gateway(net, rules=(rule,)).request("x", "GET", f"https://{host}/")
    assert net.connected == []


def test_dns_failure_is_a_clean_refusal():
    def boom(host, port):
        raise OSError("sem dns")
    g = EgressGateway((Rule("api", "https", "api.example.org"),), resolver=boom, transport=Net().transport)
    with pytest.raises(EgressBlocked) as error:
        g.request("api", "GET", "https://api.example.org/")
    assert error.value.code == "dns_failure"


# --- redirects ------------------------------------------------------------------------------------------------

def test_redirect_to_a_destination_outside_the_allowlist_is_refused():
    net = Net(responses=[(302, {"Location": "https://evil.example.net/steal"}, b"")])
    with pytest.raises(EgressBlocked) as error:
        gateway(net).request("api", "GET", "https://api.example.org/x")
    assert error.value.code == "destination_not_allowed" and len(net.connected) == 1


@pytest.mark.parametrize("location", ["http://api.example.org/x", "https://127.0.0.1/x", "//evil.example.net/x", "file:///etc/passwd"])
def test_hostile_redirect_targets_are_refused(location):
    net = Net(responses=[(301, {"Location": location}, b"")])
    with pytest.raises(EgressBlocked):
        gateway(net).request("api", "GET", "https://api.example.org/x")
    assert len(net.connected) == 1


def test_redirect_to_another_allowed_origin_drops_credentials():
    rules = (Rule("api", "https", "a.example.org", allow_auth=True), Rule("api", "https", "b.example.org", allow_auth=True))
    net = Net(responses=[(302, {"Location": "https://b.example.org/y"}, b""), (200, {}, b"{}")])
    gateway(net, rules=rules).request("api", "GET", "https://a.example.org/x", headers={"Authorization": "Bearer abc"})
    assert "Authorization" in net.connected[0]["headers"] and "Authorization" not in net.connected[1]["headers"]
    assert net.connected[1]["host"] == "b.example.org"


def test_post_redirect_and_redirect_loops_are_refused():
    net = Net(responses=[(307, {"Location": "https://api.example.org/y"}, b"")])
    with pytest.raises(EgressBlocked) as error:
        gateway(net).request("api", "POST", "https://api.example.org/x", body=b"{}")
    assert error.value.code == "redirect_blocked"
    loop = Net(responses=[(302, {"Location": "https://api.example.org/x"}, b"")])
    with pytest.raises(EgressBlocked) as error:
        gateway(loop).request("api", "GET", "https://api.example.org/x")
    assert error.value.code == "too_many_redirects" and len(loop.connected) == 4


def test_redirect_to_an_allowed_name_that_now_resolves_to_a_private_address_is_refused():
    rules = (Rule("api", "https", "a.example.org"), Rule("api", "https", "b.example.org"))
    net = Net(answers={"b.example.org": ["10.0.0.9"]}, responses=[(302, {"Location": "https://b.example.org/"}, b"")])
    with pytest.raises(EgressBlocked) as error:
        gateway(net, rules=rules).request("api", "GET", "https://a.example.org/")
    assert error.value.code == "private_address"


# --- carga: caminhos e credenciais ----------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    "ver C:\\Users\\operador\\roteiro.txt", "ver D:/dados/x", "leia /home/yann/projeto", "file:///etc/passwd", "~/segredo",
    "api_key = abc123", "Authorization: Bearer abcdefgh12345678", SECRET, "password: hunter2",
])
def test_payload_with_local_paths_or_credentials_is_refused(body):
    net = Net()
    with pytest.raises(EgressBlocked) as error:
        gateway(net).request("api", "POST", "https://api.example.org/x", body=body)
    assert error.value.code == "payload_private" and net.connected == []


def test_json_payload_and_query_are_scanned_too():
    net = Net()
    g = gateway(net)
    with pytest.raises(EgressBlocked):
        g.request("api", "POST", "https://api.example.org/x", body={"nested": {"path": "C:\\Users\\a\\b"}})
    with pytest.raises(EgressBlocked):
        g.request("api", "GET", "https://api.example.org/x?token=" + "z" * 30)
    assert net.connected == []


def test_urls_in_the_payload_are_not_mistaken_for_drive_paths():
    net = Net()
    gateway(net).request("api", "POST", "https://api.example.org/x", body={"fonte": "https://example.org/video"})
    assert len(net.connected) == 1


# --- limites e erros ------------------------------------------------------------------------------------------

def test_oversized_response_and_transport_errors_are_refused():
    big = Net(responses=[(200, {}, b"x" * 2000)])
    with pytest.raises(EgressBlocked) as error:
        gateway(big).request("api", "GET", "https://api.example.org/x", max_bytes=1000)
    assert error.value.code == "response_too_large"

    class Broken(Net):
        def transport(self, *args):
            raise OSError("reset")
    with pytest.raises(EgressBlocked) as error:
        gateway(Broken()).request("api", "GET", "https://api.example.org/x")
    assert error.value.code == "transport_error"


# --- Hermes (loopback explícito) ------------------------------------------------------------------------------

def test_hermes_is_loopback_http_with_explicit_port_only():
    g = EgressGateway(())
    g.authorize("hermes", "POST", "http://127.0.0.1:8642/v1/runs", body=b"{}", headers={"Authorization": "Bearer abc"})
    g.authorize("hermes", "GET", "http://[::1]:8642/v1/runs/x")
    for url in ("http://127.0.0.1/v1/runs", "https://127.0.0.1:8642/v1/runs", "http://10.0.0.5:8642/v1/runs",
                "http://localhost:8642/v1/runs", "http://api.provedor.com:8642/v1/runs", "https://api.openai.com/v1/chat"):
        with pytest.raises(EgressBlocked):
            g.authorize("hermes", "GET", url)
    with pytest.raises(EgressBlocked):
        EgressGateway((), hermes_loopback=False).authorize("hermes", "GET", "http://127.0.0.1:8642/v1/runs")


def test_hermes_envelope_with_private_data_is_refused():
    g = EgressGateway(())
    with pytest.raises(EgressBlocked) as error:
        g.authorize("hermes", "POST", "http://127.0.0.1:8642/v1/runs", body=json.dumps({"input": "ler C:\\Users\\x\\y"}).encode())
    assert error.value.code == "payload_private"


def test_hostname_destinations_are_judged_by_name_without_dns_in_authorize():
    net = Net()
    g = gateway(net)
    g.authorize("api", "GET", "https://api.example.org/x")
    assert net.resolved == [] and net.connected == []


# --- configuração explícita -----------------------------------------------------------------------------------

def test_parse_rules_accepts_explicit_destinations_only():
    rules = parse_rules("modelo=https://api.provedor.com,auth,methods=GET+POST; hermes_alt=http://127.0.0.1:9000,loopback,auth")
    assert rules[0] == Rule("modelo", "https", "api.provedor.com", None, False, True, frozenset({"GET", "POST"}))
    assert rules[1].allow_loopback and rules[1].port == 9000
    assert parse_rules("") == ()
    for bad in ("x=https://a.com", "modelo=http://api.provedor.com", "Modelo=https://a.com", "m=https://u:p@a.com", "m=https://a.com/path",
                "m=https://a.com?q=1", "m=ftp://a.com", "m=https://a.com,magia", "=https://a.com"):
        with pytest.raises(ValueError):
            parse_rules(bad)


def test_env_allowlist_is_the_only_way_to_open_a_new_destination(monkeypatch):
    monkeypatch.setenv("HORUS_EGRESS_ALLOW", "modelo=https://api.provedor.com,auth,methods=GET+POST")
    egress.configure(None)
    g = egress.gateway()
    g.authorize("modelo", "POST", "https://api.provedor.com/v1/x", body=b"{}")
    with pytest.raises(EgressBlocked):
        g.authorize("modelo", "POST", "https://outro.provedor.com/v1/x")
    egress.configure(None)


# --- auditoria em transmissions (HOR-115) ---------------------------------------------------------------------

def test_every_decision_is_audited_without_content(log):
    net = Net()
    g = gateway(net, log)
    g.request("api", "POST", "https://api.example.org/x", body={"nota": "conteudo-confidencial-123"})
    with pytest.raises(EgressBlocked):
        g.request("api", "POST", "https://api.example.org/x", body="ver C:\\Users\\yann\\a " + SECRET)
    with pytest.raises(EgressBlocked):
        g.request("api", "GET", "https://evil.example.net/x")
    with pytest.raises(EgressBlocked):
        g.authorize("api", "GET", "https://evil.example.net/y")
    records = rows(log)
    assert [r["result"] for r in records] == ["sent", "policy_violation", "policy_violation", "policy_violation"]
    assert [r["kind"] for r in records] == ["submit", "gate_refusal", "gate_refusal", "gate_refusal"]
    assert [r["detail"] for r in records] == ["http_200", "payload_private", "destination_not_allowed", "destination_not_allowed"]
    assert all(r["stage"] == "egress:api" and r["provider"] == "egress" for r in records)
    assert records[0]["destination"] == "https://api.example.org:443" and records[0]["envelope_bytes"] > 0 and len(records[0]["envelope_sha256"]) == 64
    blob = json.dumps(records)
    for forbidden in ("conteudo-confidencial", "C:\\\\Users", SECRET, "yann"):
        assert forbidden not in blob


def test_audit_failure_never_changes_the_decision(log):
    class Broken:
        def best_effort(self, *a, **k):
            raise RuntimeError("falha")
    g = EgressGateway((Rule("api", "https", "api.example.org"),), audit=Broken(), resolver=Net().resolver, transport=Net().transport)
    # best_effort real nunca levanta; um auditor defeituoso não deve virar permissão: a recusa continua recusa
    with pytest.raises(Exception):
        g.request("api", "GET", "https://evil.example.net/")


def test_transmissions_remain_append_only(log):
    gateway(Net(), log).authorize("api", "GET", "https://api.example.org/x")
    with log.store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM transmissions")


# --- integração: os pontos de saída reais passam pelo gateway --------------------------------------------------

def test_hermes_run_client_consults_the_gateway_before_any_socket(monkeypatch):
    from horus_backend.workflow_hermes import HermesRunClient
    client = HermesRunClient("http://127.0.0.1:8642", "t" * 24)

    def never(*args, **kwargs):
        raise AssertionError("socket aberto apesar da recusa")
    monkeypatch.setattr(client.opener, "open", never)
    egress.configure(EgressGateway(()))
    try:
        with pytest.raises(EgressBlocked) as error:
            client.submit({"input": "ler C:\\Users\\operador\\segredo.txt"}, "key-1")
        assert error.value.code == "payload_private"
        egress.configure(EgressGateway((), hermes_loopback=False))
        with pytest.raises(EgressBlocked):
            client.status("run1")
    finally:
        egress.configure(None)


def test_youtube_download_job_requires_an_allowlisted_destination(tmp_path):
    from horus_backend.config import Settings
    from horus_backend.jobs import DownloadRequest, JobManager
    settings = Settings(token="egress-tests-token-0123456789", data_dir=tmp_path / "data", factory_root=None, hermes_executable=None,
                        hermes_config=tmp_path / "h.json")
    manager = JobManager(Store(settings.data_dir / "horus.sqlite3"), settings)
    out = tmp_path / "saida"
    out.mkdir()
    request = DownloadRequest(operation="youtube.download", url="https://youtu.be/dQw4w9WgXcQ", directory=str(out))
    egress.configure(EgressGateway(()))
    try:
        with pytest.raises(EgressBlocked):
            manager.command(request)
        egress.configure(EgressGateway(DEFAULT_RULES))
        assert "download" in manager.command(request)
    finally:
        egress.configure(None)


def test_radar_transport_consults_the_gateway_without_leaking_the_key(log, monkeypatch):
    from horus_backend.radar_youtube.transport import GoogleTransport, CollectionError
    egress.configure(EgressGateway((), audit=log))
    try:
        transport = GoogleTransport("AIza" + "b" * 35, allow_network=True)
        with pytest.raises(EgressBlocked):
            transport.get("videos", {"id": "x"})
    finally:
        egress.configure(None)
    blob = json.dumps(rows(log))
    assert "AIza" not in blob and rows(log)[0]["destination"] == "https://www.googleapis.com:443"


def test_authorized_hermes_calls_do_not_duplicate_the_hermes_ledger_but_refusals_and_other_purposes_are_logged(log):
    g = EgressGateway(DEFAULT_RULES, audit=log)
    g.authorize("hermes", "GET", "http://127.0.0.1:8642/v1/runs/x")
    assert rows(log) == []
    g.authorize("youtube_download", "GET", "https://youtu.be/dQw4w9WgXcQ")
    with pytest.raises(EgressBlocked):
        g.authorize("hermes", "GET", "http://10.0.0.5:8642/v1/runs/x")
    assert [(r["stage"], r["result"], r["detail"]) for r in rows(log)] == [
        ("egress:youtube_download", "attempted", "authorized"), ("egress:hermes", "policy_violation", "destination_not_allowed")]


# --- paridade com o validador de URL do download ---------------------------------------------------------------

CANDIDATE_HOSTS = ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "gaming.youtube.com",
                   "studio.youtube.com", "tv.youtube.com", "kids.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com",
                   "evil-youtube.com", "youtube.com.evil.net", "googlevideo.com"]


def sample_url(host):
    if host == "youtu.be":
        return "https://youtu.be/dQw4w9WgXcQ"
    return f"https://{host}/watch?v=dQw4w9WgXcQ"


def test_download_allowlist_matches_the_hosts_the_url_validator_accepts():
    """Falha se `is_youtube_url` passar a aceitar um host que o gateway recusa (download quebrado) ou o contrário (allowlist maior que o validador)."""
    from horus_backend.transcriber import YOUTUBE_DOWNLOAD_HOSTS
    accepted = {h for h in CANDIDATE_HOSTS if is_youtube_url(sample_url(h))}
    allowed = {r.host for r in DEFAULT_RULES if r.purpose == "youtube_download"}
    assert accepted == allowed == set(YOUTUBE_DOWNLOAD_HOSTS), (accepted ^ allowed)
    gateway_ = EgressGateway(DEFAULT_RULES)
    for host in accepted:
        gateway_.authorize("youtube_download", "GET", sample_url(host))      # nenhum host aceito é recusado pelo gateway
    for host in set(CANDIDATE_HOSTS) - accepted:
        with pytest.raises(EgressBlocked):
            gateway_.authorize("youtube_download", "GET", sample_url(host))
