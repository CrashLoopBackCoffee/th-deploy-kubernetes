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


class MetallbConfig(StrictBaseModel):
    start: ipaddress.IPv4Address
    end: ipaddress.IPv4Address


class MicroK8sInstanceConfig(StrictBaseModel):
    name: str
    cores: int
    memory_min: int = pydantic.Field(alias='memory-min')
    memory_max: int = pydantic.Field(alias='memory-max')
    disks: list[DiskConfig]
    address: ipaddress.IPv4Interface


class MicroK8sConfig(StrictBaseModel):
    vlan: int | None = None
    cloud_image: str = pydantic.Field(
        alias='cloud-image',
        default='https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img',
    )
    ssh_public_key: str = pydantic.Field(alias='ssh-public-key')
    master_nodes: list[MicroK8sInstanceConfig] = pydantic.Field(alias='master-nodes')
    metallb: MetallbConfig
    version: str


class CertManagerConfig(StrictBaseModel):
    use_staging: bool = False

    @property
    def issuer_server(self):
        return (
            'https://acme-staging-v02.api.letsencrypt.org/directory'
            if self.use_staging
            else 'https://acme-v02.api.letsencrypt.org/directory'
        )


class ComponentConfig(StrictBaseModel):
    cert_manager: CertManagerConfig = pydantic.Field(
        alias='cert-manager', default=CertManagerConfig()
    )
    cloudflare: deploy_base.model.CloudflareConfig
    proxmox: ProxmoxConfig
    microk8s: MicroK8sConfig


class StackConfig(StrictBaseModel):
    model_config = {'alias_generator': lambda field_name: f'{get_pulumi_project()}:{field_name}'}
    config: ComponentConfig


class PulumiConfigRoot(StrictBaseModel):
    config: StackConfig
