import whois
import datetime
from pprint import pprint

def test_whois():
    domain = "haoquan.me"
    whois_info = whois.whois(domain)
    pprint(whois_info)
    assert isinstance(whois_info, dict)
    assert "domain_name" in whois_info
    assert isinstance(whois_info["domain_name"], str)
    assert "creation_date" in whois_info
    assert isinstance(whois_info["creation_date"], datetime.datetime)
    assert "expiration_date" in whois_info
    assert isinstance(whois_info["expiration_date"], datetime.datetime)