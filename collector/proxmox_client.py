from typing import Any, Dict, List
from proxmoxer import ProxmoxAPI


class ProxmoxClient:
    """Proxmox VE REST API client backed by proxmoxer (API token auth)."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        token_name: str,
        token_value: str,
        verify_ssl: bool = False,
    ):
        self._prox = ProxmoxAPI(
            host,
            port=port,
            user=username,          # e.g. "monitoring@pam"
            token_name=token_name,
            token_value=token_value,
            verify_ssl=verify_ssl,
            timeout=15,
        )

    # ------------------------------------------------------------------ Cluster

    def get_cluster_status(self) -> List[Dict]:
        return self._prox.cluster.status.get()

    def get_cluster_resources(self) -> List[Dict]:
        return self._prox.cluster.resources.get()

    def get_ha_status(self) -> List[Dict]:
        return self._prox.cluster.ha.status.current.get()

    def get_ha_resources(self) -> List[Dict]:
        return self._prox.cluster.ha.resources.get()

    def get_replication_jobs(self) -> List[Dict]:
        return self._prox.cluster.replication.get()

    def get_backup_jobs(self) -> List[Dict]:
        return self._prox.cluster.backup.get()

    def get_not_backed_up(self) -> List[Dict]:
        return self._prox('cluster/backup-info/not-backed-up').get()

    def get_ceph_status(self) -> Dict:
        return self._prox.cluster.ceph.status.get()

    def get_ceph_flags(self) -> List[Dict]:
        return self._prox.cluster.ceph.flags.get()

    # ------------------------------------------------------------------ Nodes

    def get_nodes(self) -> List[Dict]:
        return self._prox.nodes.get()

    def get_node_status(self, node: str) -> Dict:
        return self._prox.nodes(node).status.get()

    def get_node_version(self, node: str) -> Dict:
        return self._prox.nodes(node).version.get()

    def get_node_services(self, node: str) -> List[Dict]:
        return self._prox.nodes(node).services.get()

    def get_node_subscription(self, node: str) -> Dict:
        return self._prox.nodes(node).subscription.get()

    def get_node_apt_updates(self, node: str) -> List[Dict]:
        return self._prox.nodes(node).apt.update.get()

    def get_node_tasks(self, node: str) -> List[Dict]:
        return self._prox.nodes(node).tasks.get()

    def get_node_replication(self, node: str) -> List[Dict]:
        return self._prox.nodes(node).replication.get()

    def get_node_netstat(self, node: str) -> List[Dict]:
        return self._prox.nodes(node).netstat.get()

    def get_node_rrddata(self, node: str, timeframe: str = "hour") -> List[Dict]:
        """Historical node metrics. timeframe: hour | day | week | month | year"""
        return self._prox.nodes(node).rrddata.get(timeframe=timeframe, cf="AVERAGE")

    # ------------------------------------------------------------------ Disks

    def get_node_disks(self, node: str) -> List[Dict]:
        return self._prox.nodes(node).disks.list.get()

    def get_disk_smart(self, node: str, disk: str) -> Dict:
        return self._prox.nodes(node).disks.smart.get(disk=disk)

    # ------------------------------------------------------------------ VMs

    def get_vms(self, node: str) -> List[Dict]:
        return self._prox.nodes(node).qemu.get()

    def get_vm_status(self, node: str, vmid: int) -> Dict:
        return self._prox.nodes(node).qemu(vmid).status.current.get()

    def get_vm_config(self, node: str, vmid: int) -> Dict:
        return self._prox.nodes(node).qemu(vmid).config.get()

    def get_vm_snapshots(self, node: str, vmid: int) -> List[Dict]:
        return self._prox.nodes(node).qemu(vmid).snapshot.get()

    def get_vm_rrddata(self, node: str, vmid: int, timeframe: str = "hour") -> List[Dict]:
        return self._prox.nodes(node).qemu(vmid).rrddata.get(timeframe=timeframe, cf="AVERAGE")

    def get_vm_agent_fsinfo(self, node: str, vmid: int) -> List[Dict]:
        data = self._prox.nodes(node).qemu(vmid).agent('get-fsinfo').get()
        if isinstance(data, dict):
            return data.get("result", [])
        return data or []

    def get_vm_agent_network(self, node: str, vmid: int) -> List[Dict]:
        data = self._prox.nodes(node).qemu(vmid).agent('network-get-interfaces').get()
        if isinstance(data, dict):
            return data.get("result", [])
        return data or []

    # ------------------------------------------------------------------ LXC

    def get_containers(self, node: str) -> List[Dict]:
        return self._prox.nodes(node).lxc.get()

    def get_container_status(self, node: str, vmid: int) -> Dict:
        return self._prox.nodes(node).lxc(vmid).status.current.get()

    def get_container_config(self, node: str, vmid: int) -> Dict:
        return self._prox.nodes(node).lxc(vmid).config.get()

    def get_container_snapshots(self, node: str, vmid: int) -> List[Dict]:
        return self._prox.nodes(node).lxc(vmid).snapshot.get()

    def get_container_rrddata(self, node: str, vmid: int, timeframe: str = "hour") -> List[Dict]:
        return self._prox.nodes(node).lxc(vmid).rrddata.get(timeframe=timeframe, cf="AVERAGE")

    # ------------------------------------------------------------------ Storage

    def get_storage(self, node: str) -> List[Dict]:
        return self._prox.nodes(node).storage.get()

    def get_storage_status(self, node: str, storage: str) -> Dict:
        return self._prox.nodes(node).storage(storage).status.get()

    def get_storage_content(self, node: str, storage: str) -> List[Dict]:
        return self._prox.nodes(node).storage(storage).content.get()

    def get_storage_rrddata(self, node: str, storage: str, timeframe: str = "hour") -> List[Dict]:
        return self._prox.nodes(node).storage(storage).rrddata.get(timeframe=timeframe, cf="AVERAGE")

    # ------------------------------------------------------------------ Ceph (node-scoped)

    def get_ceph_osds(self, node: str) -> Dict:
        return self._prox.nodes(node).ceph.osd.get()

    def get_ceph_pools(self, node: str) -> List[Dict]:
        return self._prox.nodes(node).ceph.pool.get()

    def get_ceph_mons(self, node: str) -> List[Dict]:
        return self._prox.nodes(node).ceph.mon.get()
