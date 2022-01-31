from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Type

from blacksmith import (
    HTTPTimeout,
    PrometheusMetrics,
    SyncAbstractServiceDiscovery,
    SyncClient,
    SyncClientFactory,
    SyncConsulDiscovery,
    SyncHTTPMiddleware,
    SyncRouterDiscovery,
    SyncStaticDiscovery,
)
from blacksmith.typing import ClientName
from django.http.request import HttpRequest
from django.utils.module_loading import import_string

from dj_blacksmith._settings import get_clients
from dj_blacksmith.client._sync.middleware import SyncHTTPMiddlewareBuilder


def build_sd(
    settings: Mapping[str, Mapping[str, Any]]
) -> SyncAbstractServiceDiscovery:
    sd_setting = settings.get("sd")
    if sd_setting == "consul":
        return SyncConsulDiscovery(**settings["consul_sd_config"])
    elif sd_setting == "router":
        return SyncRouterDiscovery(**settings["router_sd_config"])
    elif sd_setting == "static":
        endpoints: Dict[Tuple[str, Optional[str]], str] = {}
        for key, val in settings["static_sd_config"].items():
            if "/" in key:
                srv, ver = key.rsplit("/", 1)
            else:
                srv, ver = key, None
            endpoints[(srv, ver)] = val
        return SyncStaticDiscovery(endpoints)
    else:
        raise RuntimeError(f"Unkown service discovery {sd_setting}")


def build_middlewares(
    settings: Mapping[str, Any],
    metrics: PrometheusMetrics,
) -> Iterable[SyncHTTPMiddleware]:
    middlewares: List[str] = settings.get("middlewares", [])
    for middleware in middlewares:
        cls: Type[SyncHTTPMiddlewareBuilder] = import_string(middleware)
        yield cls(settings, metrics).build()


def client_factory(name: str = "default") -> SyncClientFactory[Any, Any]:
    settings = get_clients().get(name)
    if settings is None:
        raise RuntimeError(f"Client {name} does not exists")
    sd = build_sd(settings)
    timeout = settings.get("timeout", {})
    cli: SyncClientFactory[Any, Any] = SyncClientFactory(
        sd,
        proxies=settings.get("proxies"),
        verify_certificate=settings.get("verify_certificate", True),
        timeout=HTTPTimeout(**timeout),
    )
    metrics = PrometheusMetrics()
    for middleware in build_middlewares(settings, metrics):
        cli.add_middleware(middleware)
    cli.initialize()
    return cli


class SyncClientProxy:
    def __init__(
        self, client_factory: SyncClientFactory[Any, Any], request: HttpRequest
    ):
        self.client_factory = client_factory
        self.request = request

    def __call__(self, client_name: ClientName) -> SyncClient[Any, Any]:
        cli = self.client_factory(client_name)
        return cli


class SyncDjBlacksmithClient:
    client_factories: Dict[str, SyncClientFactory[Any, Any]] = {}

    def __init__(self, request: HttpRequest):
        self.request = request

    def __call__(
        self, factory_name: str = "default"
    ) -> SyncClientProxy:

        if factory_name not in self.client_factories:
            self.client_factories[factory_name] = client_factory(factory_name)

        return SyncClientProxy(self.client_factories[factory_name], self.request)
