import ipaddress
import pathlib

import deploy_base.model
import pydantic

REPO_PREFIX = 'deploy-'


def get_pulumi_project():
    repo_dir = pathlib.Path().resolve()

    while not repo_dir.name.startswith(REPO_PREFIX):
        if not repo_dir.parents:
            raise ValueError('Could not find repo root')

        repo_dir = repo_dir.parent
    return repo_dir.name[len(REPO_PREFIX) :]


class StrictBaseModel(pydantic.BaseModel):
    model_config = {'extra': 'forbid'}


class PulumiSecret(StrictBaseModel):
    secure: pydantic.SecretStr

    def __str__(self):
        return str(self.secure)


class ProxmoxConfig(StrictBaseModel):
    api_token: deploy_base.model.OnePasswordRef = pydantic.Field(alias='api-token')
    api_endpoint: str = pydantic.Field(alias='api-endpoint')
    node_name: str = pydantic.Field(alias='node-name')
    insecure: bool = False


class DiskConfig(StrictBaseModel):
    size: int


class TalosInstanceConfig(StrictBaseModel):
    nodes: int = 1
    cores: int
    memory_min: int = pydantic.Field(alias='memory-min')
    memory_max: int = pydantic.Field(alias='memory-max')
    disks: list[DiskConfig]
    start_address: ipaddress.IPv4Address = pydantic.Field(alias='start-address')


class TalosConfig(StrictBaseModel):
    version: str
    vlan: int | None = None
    control_plane: TalosInstanceConfig = pydantic.Field(alias='control-plane')
    cluster_endpoint_address: str = pydantic.Field(alias='cluster-endpoint-address')
    worker: TalosInstanceConfig
    network: ipaddress.IPv4Network


class ComponentConfig(StrictBaseModel):
    proxmox: ProxmoxConfig
    talos: TalosConfig


class StackConfig(StrictBaseModel):
    model_config = {'alias_generator': lambda field_name: f'{get_pulumi_project()}:{field_name}'}
    config: ComponentConfig


class PulumiConfigRoot(StrictBaseModel):
    config: StackConfig
