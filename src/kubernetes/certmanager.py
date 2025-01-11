import pulumi as p
import pulumi_cloudflare as cloudflare
import pulumi_command as command
import pulumi_kubernetes as k8s

from kubernetes.config import ComponentConfig


def create_certmanager(
    component_config: ComponentConfig,
    connection_args: command.remote.ConnectionArgs,
    cloudflare_provider: cloudflare.Provider,
    k8s_provider: k8s.Provider,
) -> None:
    # Install cert-manager
    cert_manager = command.remote.Command(
        'cert-manager',
        connection=connection_args,
        add_previous_output_in_env=False,
        create='microk8s enable cert-manager',
        delete='microk8s disable cert-manager',
    )

    # Create scoped down cloudflare token
    cloud_config = cloudflare.ApiToken(
        'cloudflare-token',
        name=f'microk8s-{p.get_stack()}-cert-manager',
        policies=[
            {
                'effect': 'allow',
                'permission_groups': [
                    # Zone Read
                    'c8fed203ed3043cba015a93ad1616f1f',
                    # DNS Write
                    '4755a26eedb94da69e1066d98aa820be',
                ],
                'resources': {'com.cloudflare.api.account.zone.*': '*'},
            },
        ],
        opts=p.ResourceOptions(provider=cloudflare_provider),
    )

    k8s_opts = p.ResourceOptions(provider=k8s_provider, depends_on=[cert_manager])

    # Cloudflare DNS API Secret
    cloudflare_secret = k8s.core.v1.Secret(
        'cloudflare-api-token',
        metadata={'namespace': 'cert-manager'},
        type='Opaque',
        string_data={'api-token': cloud_config.value},
        opts=k8s_opts,
    )

    # Issuer
    k8s.apiextensions.CustomResource(
        'letsencrypt-issuer',
        api_version='cert-manager.io/v1',
        kind='ClusterIssuer',
        metadata={'namespace': 'cert-manager', 'name': 'lets-encrypt'},
        spec={
            'acme': {
                'server': component_config.cert_manager.issuer_server,
                'email': component_config.cloudflare.email,
                'privateKeySecretRef': {'name': 'lets-encrypt-private-key'},
                'solvers': [
                    {
                        'dns01': {
                            'cloudflare': {
                                'apiTokenSecretRef': {
                                    'name': cloudflare_secret.metadata.name,
                                    'key': 'api-token',
                                },
                            },
                        },
                    },
                ],
            },
        },
        opts=k8s_opts,
    )
