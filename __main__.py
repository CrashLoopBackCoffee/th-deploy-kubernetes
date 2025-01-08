import pulumi as p
import pulumi_proxmoxve as proxmoxve

from kubernetes.config import ComponentConfig
from kubernetes.talos import create_talos

component_config = ComponentConfig.model_validate(p.Config().get_object('config'))

token_output = component_config.proxmox.api_token.value

proxmox_provider = proxmoxve.Provider(
    'proxmox',
    endpoint=component_config.proxmox.api_endpoint,
    api_token=token_output,
    insecure=component_config.proxmox.insecure,
)

create_talos(component_config, proxmox_provider)
