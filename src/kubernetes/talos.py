import collections.abc as c
import ipaddress
import typing as t

import pulumi as p
import pulumi_proxmoxve as proxmoxve
import pulumiverse_talos as talos

from kubernetes.config import ComponentConfig

SCHEMATIC = """
customization:
    systemExtensions:
        officialExtensions:
            - siderolabs/qemu-guest-agent
"""
"""Schematic for images needed on Proxmox VM."""

DATA_MOUNTPOINT = '/var/mnt/data'
"""Directory, where data disk is mounted on node."""

LABEL_DATA_VOLUME = 'tobiash.net/data-volume'
"""Label used to mark node as having a data volume."""


# resolve nested outputs, see https://github.com/pulumiverse/pulumi-talos/issues/93:
class ClientConfigurationArgs(t.Protocol):
    def __init__(self, *, ca_certificate, client_certificate, client_key): ...


def _get_client_configuration_as[T: ClientConfigurationArgs](
    client_configuration: p.Output[talos.machine.outputs.ClientConfiguration],
    type_: type[T],
) -> T:
    return type_(
        ca_certificate=client_configuration.ca_certificate,
        client_certificate=client_configuration.client_certificate,
        client_key=client_configuration.client_key,
    )


def _get_network_configuration(
    hostname: p.Input[str],
    address: ipaddress.IPv4Address,
    network: ipaddress.IPv4Network,
) -> dict[str, t.Any]:
    return {
        'network': {
            'hostname': hostname,
            'interfaces': [
                {
                    'deviceSelector': {'physical': True},
                    'addresses': [f'{address}/{network.prefixlen}'],
                    'dhcp': False,
                    'routes': [
                        {
                            'network': '0.0.0.0/0',
                            'gateway': str(network.network_address + 1),
                        }
                    ],
                },
            ],
            'nameservers': [
                str(network.network_address + 1),
            ],
        }
    }


def get_images(version: str) -> tuple[p.Output[str], p.Output[str]]:
    """Retrieve image URLs for needed configuration (hardcoded for now)."""

    schematic = talos.imagefactory.Schematic('talos-schematic', schematic=SCHEMATIC)

    urls = talos.imagefactory.get_urls_output(
        platform='metal',
        architecture='amd64',
        talos_version=version,
        schematic_id=schematic.id,
    ).urls

    return urls.iso, urls.installer


def get_vm_ipv4(vm: proxmoxve.vm.VirtualMachine) -> p.Output[str]:
    def get_eth_interface_index(interface_names: c.Sequence[str]) -> int:
        for index, name in enumerate(interface_names):
            if name.startswith('en'):
                return index

        raise ValueError(
            f'No ethernet interface found for VM {vm.name!r}.',
            interface_names,
        )

    eth_interface_index = vm.network_interface_names.apply(get_eth_interface_index)
    return eth_interface_index.apply(lambda index: vm.ipv4_addresses[index][0])


def create_talos(component_config: ComponentConfig, proxmox_provider: proxmoxve.Provider) -> None:
    talos_config = component_config.talos

    iso_image_url, installer_image_url = get_images(talos_config.version)
    p.export('iso', iso_image_url)

    proxmox_opts = p.ResourceOptions(provider=proxmox_provider)
    stack_name = p.get_stack()

    iso_image = proxmoxve.download.File(
        'talos-iso',
        content_type='iso',
        datastore_id='local',
        node_name=component_config.proxmox.node_name,
        overwrite=False,
        url=iso_image_url,
        file_name=f'talos-{talos_config.version}-{stack_name}.iso',
        opts=p.ResourceOptions.merge(proxmox_opts, p.ResourceOptions(delete_before_replace=True)),
    )

    # Create control plane node
    vlan_config: proxmoxve.vm.VirtualMachineNetworkDeviceArgsDict = (
        {'vlan_id': talos_config.vlan} if talos_config.vlan else {}
    )
    tags = [f'talos-{stack_name}']

    control_plane_config = talos_config.control_plane
    control_plane_disks: list[proxmoxve.vm.VirtualMachineDiskArgsDict] = [
        {
            'interface': f'virtio{idx}',
            'size': disk.size,
            'discard': 'on',
            'iothread': True,
            'datastore_id': 'local-lvm',
            'file_format': 'raw',
            # Hack to avoid diff in subsequent runs
            'speed': {
                'read': 10000,
            },
        }
        for idx, disk in enumerate(control_plane_config.disks)
    ]

    control_plane_vms = [
        proxmoxve.vm.VirtualMachine(
            f'talos-control-plane-{i}',
            name=f'talos-control-plane-{i}',
            node_name=component_config.proxmox.node_name,
            cpu={'cores': control_plane_config.cores, 'type': 'host'},
            memory={
                'dedicated': control_plane_config.memory_max,
                'floating': control_plane_config.memory_min,
            },
            cdrom={'enabled': True, 'file_id': iso_image.id},
            disks=control_plane_disks,
            machine='q35',
            network_devices=[{'bridge': 'vmbr0', 'model': 'virtio', **vlan_config}],
            boot_orders=['virtio0', 'ide3'],
            agent={'enabled': True},
            stop_on_destroy=True,
            tags=tags,
            opts=p.ResourceOptions.merge(proxmox_opts, p.ResourceOptions(ignore_changes=['cdrom'])),
        )
        for i in range(talos_config.control_plane.nodes)
    ]

    # Create worker nodes
    worker_config = talos_config.worker
    worker_disks: list[proxmoxve.vm.VirtualMachineDiskArgsDict] = [
        {
            'interface': f'virtio{idx}',
            'size': disk.size,
            'discard': 'on',
            'iothread': True,
            'datastore_id': 'local-lvm',
            'file_format': 'raw',
            # Hack to avoid diff in subsequent runs
            'speed': {
                'read': 10000,
            },
        }
        for idx, disk in enumerate(worker_config.disks)
    ]
    worker_vms = [
        proxmoxve.vm.VirtualMachine(
            f'talos-worker-{i}',
            name=f'talos-worker-{i}',
            node_name=component_config.proxmox.node_name,
            cpu={'cores': worker_config.cores, 'type': 'host'},
            memory={
                'dedicated': worker_config.memory_max,
                'floating': worker_config.memory_min,
            },
            cdrom={'enabled': True, 'file_id': iso_image.id},
            disks=worker_disks,
            machine='q35',
            network_devices=[{'bridge': 'vmbr0', 'model': 'virtio', **vlan_config}],
            boot_orders=['virtio0', 'ide3'],
            agent={'enabled': True},
            stop_on_destroy=True,
            tags=tags,
            opts=p.ResourceOptions.merge(proxmox_opts, p.ResourceOptions(ignore_changes=['cdrom'])),
        )
        for i in range(talos_config.worker.nodes)
    ]

    # Create control plane machine configuration
    cluster_name = f'talos-{stack_name}'
    secrets = talos.machine.Secrets('talos-secrets')
    cluster_endpoint = f'https://{talos_config.control_plane.start_address}:6443'

    cp_node_config = talos.machine.get_configuration_output(
        cluster_name=cluster_name,
        machine_type='controlplane',
        cluster_endpoint=cluster_endpoint,
        machine_secrets=talos.machine.MachineSecretsArgs(
            certs=secrets.machine_secrets.certs,
            cluster=secrets.machine_secrets.cluster,
            secrets=secrets.machine_secrets.secrets,
            trustdinfo=secrets.machine_secrets.trustdinfo,
        ),
        config_patches=p.Output.json_dumps(
            {
                'machine': {
                    'install': {
                        'image': installer_image_url,
                        'disk': '/dev/vda',
                    },
                },
                # prevent warnings about pod security by setting warn level to what is enforced:
                'cluster': {
                    'apiServer': {
                        'admissionControl': [
                            {
                                'name': 'PodSecurity',
                                'configuration': {
                                    'defaults': {
                                        'warn': 'baseline',
                                    },
                                },
                            },
                        ],
                    },
                },
            },
        ).apply(lambda o: [o]),
    )
    p.export('control-plane-config', cp_node_config.machine_configuration)

    # Create worker machine configuration
    worker_node_config = talos.machine.get_configuration_output(
        cluster_name=cluster_name,
        machine_type='worker',
        cluster_endpoint=cluster_endpoint,
        machine_secrets=talos.machine.MachineSecretsArgs(
            certs=secrets.machine_secrets.certs,
            cluster=secrets.machine_secrets.cluster,
            secrets=secrets.machine_secrets.secrets,
            trustdinfo=secrets.machine_secrets.trustdinfo,
        ),
        config_patches=p.Output.json_dumps(
            {
                'machine': {
                    'install': {
                        'image': installer_image_url,
                        'disk': '/dev/vda',
                    },
                    'disks': [
                        # Mount data disk on worker nodes
                        {
                            'device': '/dev/vdb',
                            'partitions': [
                                {
                                    'mountpoint': DATA_MOUNTPOINT,
                                },
                            ],
                        },
                    ],
                    'kubelet': {
                        'extraMounts': [
                            # Mount node directory into kubelet for later PV definition
                            {
                                'source': DATA_MOUNTPOINT,
                                'destination': DATA_MOUNTPOINT,
                                'type': 'bind',
                                'options': [
                                    'bind',
                                    'rshared',
                                    'rw',
                                ],
                            }
                        ],
                    },
                    'nodeLabels': {
                        LABEL_DATA_VOLUME: 'true',
                    },
                },
            },
        ).apply(lambda o: [o]),
    )

    control_addresses = [
        str(talos_config.control_plane.start_address + i)
        for i in range(talos_config.control_plane.nodes)
    ]
    worker_addresses = [
        str(talos_config.worker.start_address + i) for i in range(talos_config.worker.nodes)
    ]
    client_configuration = talos.client.get_configuration_output(
        client_configuration=_get_client_configuration_as(
            secrets.client_configuration, talos.client.GetConfigurationClientConfigurationArgs
        ),
        cluster_name=cluster_name,
        endpoints=p.Output.all(*control_addresses),
        nodes=p.Output.all(*worker_addresses),
    )
    p.export('talos-config', client_configuration.talos_config)

    applied = []
    for idx, vm in enumerate(control_plane_vms):
        applied.append(
            talos.machine.ConfigurationApply(
                f'talos-control-plane-{idx}-config',
                client_configuration=_get_client_configuration_as(
                    secrets.client_configuration, talos.machine.ClientConfigurationArgs
                ),
                apply_mode='reboot',
                machine_configuration_input=cp_node_config.machine_configuration,
                config_patches=[
                    p.Output.json_dumps(
                        {
                            'machine': {
                                **_get_network_configuration(
                                    vm.name,
                                    talos_config.control_plane.start_address + idx,
                                    talos_config.network,
                                ),
                            },
                        },
                    )
                ],
                node=get_vm_ipv4(vm),
            )
        )

    for idx, vm in enumerate(worker_vms):
        applied.append(
            talos.machine.ConfigurationApply(
                f'talos-worker-{idx}-config',
                client_configuration=_get_client_configuration_as(
                    secrets.client_configuration, talos.machine.ClientConfigurationArgs
                ),
                machine_configuration_input=worker_node_config.machine_configuration,
                config_patches=[
                    p.Output.json_dumps(
                        {
                            'machine': {
                                **_get_network_configuration(
                                    vm.name,
                                    talos_config.worker.start_address + idx,
                                    talos_config.network,
                                ),
                            },
                        },
                    )
                ],
                node=get_vm_ipv4(vm),
            )
        )

    talos.machine.Bootstrap(
        'bootstrap',
        node=str(talos_config.control_plane.start_address),
        client_configuration=_get_client_configuration_as(
            secrets.client_configuration, talos.machine.ClientConfigurationArgs
        ),
        opts=p.ResourceOptions(depends_on=applied),
    )

    kube_config = talos.cluster.get_kubeconfig_output(
        client_configuration=_get_client_configuration_as(
            secrets.client_configuration,
            talos.cluster.GetKubeconfigClientConfigurationArgs,
        ),
        node=str(talos_config.control_plane.start_address),
    )
    p.export('kubeconfig', kube_config.kubeconfig_raw)
