from anti_scam_agent.tools import *
import json

def test_get_domain_info():
    result = _get_domain_info("haoquan.me")
    print(json.dumps(result.__dict__, indent=2, ensure_ascii=False))
    result = _get_domain_info("example.com")
    print(json.dumps(result.__dict__, indent=2, ensure_ascii=False))