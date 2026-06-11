import ipaddress
import socket

import httpx


class DomainValidationError(ValueError):
    pass


def is_safe_external_domain(domain: str) -> bool:
    try:
        ip = socket.gethostbyname(domain)
        address = ipaddress.ip_address(ip)
        return not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        )
    except Exception:
        return False


async def validate_domain_tls(domain: str, timeout: float = 10.0) -> bool:
    if not is_safe_external_domain(domain):
        return False

    try:
        async with httpx.AsyncClient(verify=True) as client:
            await client.head(
                f"https://{domain}/",
                headers={"Host": domain},
                timeout=timeout,
                follow_redirects=False,
            )
        return True
    except Exception:
        return False
