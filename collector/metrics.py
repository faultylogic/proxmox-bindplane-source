import logging
from typing import Dict, List, Optional

from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION

from .proxmox_client import ProxmoxClient

logger = logging.getLogger(__name__)

# PVE services we care about monitoring
MONITORED_SERVICES = {
    "pve-cluster", "pvedaemon", "pveproxy", "pvestatd",
    "pvescheduler", "corosync", "pve-firewall",
}


def _latest(rrddata: List[Dict], field: str) -> Optional[float]:
    """Return the most recent non-null value for a field from rrddata."""
    for entry in reversed(rrddata):
        val = entry.get(field)
        if val is not None:
            return float(val)
    return None


def setup_meter_provider(config: Dict) -> otel_metrics.Meter:
    """Configure OTel MeterProvider with OTLP exporter and return a Meter."""
    otlp_cfg = config["otlp"]
    collector_cfg = config["collector"]

    endpoint = otlp_cfg["endpoint"]
    insecure = otlp_cfg.get("insecure", True)
    headers = otlp_cfg.get("headers", {})
    interval_ms = int(collector_cfg["interval_seconds"]) * 1000

    exporter = OTLPMetricExporter(
        endpoint=endpoint,
        insecure=insecure,
        headers=headers if headers else None,
    )

    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=interval_ms,
    )

    resource = Resource(attributes={
        SERVICE_NAME: collector_cfg.get("service_name", "proxmox-collector"),
        SERVICE_VERSION: collector_cfg.get("service_version", "1.0.0"),
    })

    provider = MeterProvider(resource=resource, metric_readers=[reader])
    otel_metrics.set_meter_provider(provider)

    meter = otel_metrics.get_meter(
        collector_cfg.get("service_name", "proxmox-collector"),
        collector_cfg.get("service_version", "1.0.0"),
    )
    return meter


class ProxmoxMetricsCollector:
    """Collects Proxmox VE metrics and records them as OTel Gauge instruments."""

    def __init__(self, client: ProxmoxClient, meter: otel_metrics.Meter):
        self._client = client
        self._meter = meter
        self._setup_instruments()

    @staticmethod
    def _set(gauge, value, attributes: dict):
        """Cast value to float before recording — Proxmox API can return strings."""
        try:
            gauge.set(float(value), attributes=attributes)
        except (TypeError, ValueError):
            logger.debug("Skipping non-numeric value %r for attributes %s", value, attributes)

    def _setup_instruments(self):
        m = self._meter

        # -- Cluster
        self._cluster_nodes_total       = m.create_gauge("proxmox.cluster.nodes.total",       description="Total nodes in cluster",          unit="{node}")
        self._cluster_nodes_online      = m.create_gauge("proxmox.cluster.nodes.online",      description="Online nodes in cluster",         unit="{node}")

        # -- HA
        self._cluster_ha_quorate        = m.create_gauge("proxmox.cluster.ha.quorate",        description="Cluster quorum state (1=quorate)", unit="1")
        self._cluster_ha_resource_running = m.create_gauge("proxmox.cluster.ha.resource.running", description="HA resource running (1=started)", unit="1")

        # -- Replication
        self._cluster_replication_fail_count = m.create_gauge("proxmox.cluster.replication.fail_count", description="Replication job failure count",    unit="{failure}")
        self._cluster_replication_duration   = m.create_gauge("proxmox.cluster.replication.duration",   description="Replication job last duration",    unit="s")
        self._cluster_replication_error      = m.create_gauge("proxmox.cluster.replication.error",      description="Replication job error state (1=error)", unit="1")

        # -- Backups
        self._cluster_backup_unprotected_guests = m.create_gauge("proxmox.cluster.backup.unprotected_guests", description="Guests not covered by a backup job", unit="{guest}")

        # -- Ceph cluster-level
        self._ceph_health          = m.create_gauge("proxmox.ceph.health",          description="Ceph health (2=OK,1=WARN,0=ERR)", unit="1")
        self._ceph_osd_total       = m.create_gauge("proxmox.ceph.osd.total",       description="Total Ceph OSDs",                 unit="{osd}")
        self._ceph_osd_up          = m.create_gauge("proxmox.ceph.osd.up",          description="Up Ceph OSDs",                    unit="{osd}")
        self._ceph_osd_in          = m.create_gauge("proxmox.ceph.osd.in",          description="In Ceph OSDs",                    unit="{osd}")
        self._ceph_pg_total        = m.create_gauge("proxmox.ceph.pg.total",        description="Total Ceph placement groups",     unit="{pg}")
        self._ceph_bytes_used      = m.create_gauge("proxmox.ceph.bytes.used",      description="Ceph bytes used",                 unit="By")
        self._ceph_bytes_avail     = m.create_gauge("proxmox.ceph.bytes.avail",     description="Ceph bytes available",            unit="By")
        self._ceph_bytes_total     = m.create_gauge("proxmox.ceph.bytes.total",     description="Ceph bytes total",                unit="By")
        self._ceph_io_read_bps     = m.create_gauge("proxmox.ceph.io.read_bps",     description="Ceph read throughput",            unit="By/s")
        self._ceph_io_write_bps    = m.create_gauge("proxmox.ceph.io.write_bps",    description="Ceph write throughput",           unit="By/s")
        self._ceph_io_recovering_bps = m.create_gauge("proxmox.ceph.io.recovering_bps", description="Ceph recovery throughput",   unit="By/s")
        self._ceph_mon_count       = m.create_gauge("proxmox.ceph.mon.count",       description="Number of Ceph monitors",         unit="{monitor}")
        self._ceph_flag            = m.create_gauge("proxmox.ceph.flag",            description="Ceph flag active (1=set)",        unit="1")

        # -- Node live
        self._node_cpu_usage       = m.create_gauge("proxmox.node.cpu.usage",       description="Node CPU utilization",            unit="%")
        self._node_cpu_count       = m.create_gauge("proxmox.node.cpu.count",       description="Node logical CPU count",          unit="{cpu}")
        self._node_cpu_sockets     = m.create_gauge("proxmox.node.cpu.sockets",     description="Node CPU socket count",           unit="{socket}")
        self._node_loadavg_1m      = m.create_gauge("proxmox.node.loadavg.1m",      description="1-minute load average",           unit="1")
        self._node_loadavg_5m      = m.create_gauge("proxmox.node.loadavg.5m",      description="5-minute load average",           unit="1")
        self._node_loadavg_15m     = m.create_gauge("proxmox.node.loadavg.15m",     description="15-minute load average",          unit="1")
        self._node_memory_used     = m.create_gauge("proxmox.node.memory.used",     description="Node memory used",                unit="By")
        self._node_memory_total    = m.create_gauge("proxmox.node.memory.total",    description="Node memory total",               unit="By")
        self._node_memory_free     = m.create_gauge("proxmox.node.memory.free",     description="Node memory free",                unit="By")
        self._node_swap_used       = m.create_gauge("proxmox.node.swap.used",       description="Node swap used",                  unit="By")
        self._node_swap_total      = m.create_gauge("proxmox.node.swap.total",      description="Node swap total",                 unit="By")
        self._node_swap_free       = m.create_gauge("proxmox.node.swap.free",       description="Node swap free",                  unit="By")
        self._node_disk_used       = m.create_gauge("proxmox.node.disk.used",       description="Node root filesystem used",       unit="By")
        self._node_disk_total      = m.create_gauge("proxmox.node.disk.total",      description="Node root filesystem total",      unit="By")
        self._node_disk_avail      = m.create_gauge("proxmox.node.disk.avail",      description="Node root filesystem available",  unit="By")
        self._node_network_in      = m.create_gauge("proxmox.node.network.in",      description="Node network bytes in",           unit="By")
        self._node_network_out     = m.create_gauge("proxmox.node.network.out",     description="Node network bytes out",          unit="By")
        self._node_uptime          = m.create_gauge("proxmox.node.uptime",          description="Node uptime",                     unit="s")
        self._node_ksm_shared      = m.create_gauge("proxmox.node.ksm.shared",      description="KSM shared memory",               unit="By")

        # -- Node rrddata
        self._node_cpu_iowait         = m.create_gauge("proxmox.node.cpu.iowait",         description="Node CPU iowait",                  unit="%")
        self._node_memory_available   = m.create_gauge("proxmox.node.memory.available",   description="Node memory available (incl cache)", unit="By")
        self._node_zfs_arcsize        = m.create_gauge("proxmox.node.zfs.arcsize",        description="ZFS ARC size",                     unit="By")
        self._node_pressure_cpu_some  = m.create_gauge("proxmox.node.pressure.cpu.some",  description="CPU pressure stall (some)",         unit="%")
        self._node_pressure_io_some   = m.create_gauge("proxmox.node.pressure.io.some",   description="IO pressure stall (some)",          unit="%")
        self._node_pressure_io_full   = m.create_gauge("proxmox.node.pressure.io.full",   description="IO pressure stall (full)",          unit="%")
        self._node_pressure_mem_some  = m.create_gauge("proxmox.node.pressure.memory.some", description="Memory pressure stall (some)",    unit="%")
        self._node_pressure_mem_full  = m.create_gauge("proxmox.node.pressure.memory.full", description="Memory pressure stall (full)",    unit="%")

        # -- Node services / subscription / updates / tasks / netstat
        self._node_service_status     = m.create_gauge("proxmox.node.service.status",     description="PVE service active (1=active)",    unit="1")
        self._node_subscription_active = m.create_gauge("proxmox.node.subscription.active", description="Node subscription active (1=yes)", unit="1")
        self._node_updates_pending    = m.create_gauge("proxmox.node.updates.pending",    description="Pending APT updates",              unit="{package}")
        self._node_updates_proxmox_pending = m.create_gauge("proxmox.node.updates.proxmox_pending", description="Pending Proxmox APT updates", unit="{package}")
        self._node_tasks_errors       = m.create_gauge("proxmox.node.tasks.errors",       description="Failed tasks",                     unit="{task}")
        self._node_tasks_running      = m.create_gauge("proxmox.node.tasks.running",      description="Currently running tasks",          unit="{task}")
        self._node_netstat_in         = m.create_gauge("proxmox.node.netstat.in",         description="Guest network bytes in (netstat)", unit="By")
        self._node_netstat_out        = m.create_gauge("proxmox.node.netstat.out",        description="Guest network bytes out (netstat)", unit="By")

        # -- Physical disks
        self._node_disk_device_size   = m.create_gauge("proxmox.node.disk.device.size",   description="Physical disk size",               unit="By")
        self._node_disk_device_smart  = m.create_gauge("proxmox.node.disk.device.smart",  description="SMART health (1=PASSED,0=FAILED,-1=unknown)", unit="1")
        self._node_disk_device_smart_attr = m.create_gauge("proxmox.node.disk.device.smart_attr", description="SMART attribute raw value",  unit="1")

        # -- VM live
        self._vm_status        = m.create_gauge("proxmox.vm.status",        description="VM running (1=running)",          unit="1")
        self._vm_cpu_usage     = m.create_gauge("proxmox.vm.cpu.usage",     description="VM CPU utilization",              unit="%")
        self._vm_cpu_count     = m.create_gauge("proxmox.vm.cpu.count",     description="VM vCPU count",                   unit="{cpu}")
        self._vm_memory_used   = m.create_gauge("proxmox.vm.memory.used",   description="VM memory used",                  unit="By")
        self._vm_memory_total  = m.create_gauge("proxmox.vm.memory.total",  description="VM memory total",                 unit="By")
        self._vm_balloon_current = m.create_gauge("proxmox.vm.balloon.current", description="VM balloon current allocation", unit="By")
        self._vm_balloon_target  = m.create_gauge("proxmox.vm.balloon.target",  description="VM balloon target allocation",  unit="By")
        self._vm_disk_read     = m.create_gauge("proxmox.vm.disk.read",     description="VM disk bytes read",              unit="By")
        self._vm_disk_write    = m.create_gauge("proxmox.vm.disk.write",    description="VM disk bytes written",           unit="By")
        self._vm_disk_size     = m.create_gauge("proxmox.vm.disk.size",     description="VM primary disk size",            unit="By")
        self._vm_network_in    = m.create_gauge("proxmox.vm.network.in",    description="VM network bytes in",             unit="By")
        self._vm_network_out   = m.create_gauge("proxmox.vm.network.out",   description="VM network bytes out",            unit="By")
        self._vm_uptime        = m.create_gauge("proxmox.vm.uptime",        description="VM uptime",                       unit="s")

        # -- VM config
        self._vm_config_cores        = m.create_gauge("proxmox.vm.config.cores",        description="VM configured cores",         unit="{core}")
        self._vm_config_sockets      = m.create_gauge("proxmox.vm.config.sockets",      description="VM configured sockets",       unit="{socket}")
        self._vm_config_memory_mib   = m.create_gauge("proxmox.vm.config.memory_mib",   description="VM configured memory",        unit="MiBy")
        self._vm_config_balloon_mib  = m.create_gauge("proxmox.vm.config.balloon_mib",  description="VM configured balloon min",   unit="MiBy")
        self._vm_config_cpulimit     = m.create_gauge("proxmox.vm.config.cpulimit",     description="VM CPU limit",                unit="1")
        self._vm_config_cpuunits     = m.create_gauge("proxmox.vm.config.cpuunits",     description="VM CPU units (weight)",       unit="1")
        self._vm_config_onboot       = m.create_gauge("proxmox.vm.config.onboot",       description="VM start on boot (1=yes)",    unit="1")

        # -- VM rrddata
        self._vm_disk_used          = m.create_gauge("proxmox.vm.disk.used",          description="VM disk used (from rrd)",       unit="By")
        self._vm_memory_host        = m.create_gauge("proxmox.vm.memory.host",        description="VM host memory used",           unit="By")
        self._vm_pressure_cpu_some  = m.create_gauge("proxmox.vm.pressure.cpu.some",  description="VM CPU pressure (some)",        unit="%")
        self._vm_pressure_cpu_full  = m.create_gauge("proxmox.vm.pressure.cpu.full",  description="VM CPU pressure (full)",        unit="%")
        self._vm_pressure_io_some   = m.create_gauge("proxmox.vm.pressure.io.some",   description="VM IO pressure (some)",         unit="%")
        self._vm_pressure_mem_some  = m.create_gauge("proxmox.vm.pressure.memory.some", description="VM memory pressure (some)",   unit="%")
        self._vm_pressure_mem_full  = m.create_gauge("proxmox.vm.pressure.memory.full", description="VM memory pressure (full)",   unit="%")

        # -- VM snapshots
        self._vm_snapshot_count     = m.create_gauge("proxmox.vm.snapshot.count",     description="VM snapshot count",             unit="{snapshot}")

        # -- VM guest agent
        self._vm_agent_disk_used    = m.create_gauge("proxmox.vm.agent.disk.used",    description="Guest filesystem used bytes",   unit="By")
        self._vm_agent_disk_total   = m.create_gauge("proxmox.vm.agent.disk.total",   description="Guest filesystem total bytes",  unit="By")
        self._vm_agent_net_rx_bytes   = m.create_gauge("proxmox.vm.agent.net.rx_bytes",   description="Guest NIC RX bytes",        unit="By")
        self._vm_agent_net_tx_bytes   = m.create_gauge("proxmox.vm.agent.net.tx_bytes",   description="Guest NIC TX bytes",        unit="By")
        self._vm_agent_net_rx_errors  = m.create_gauge("proxmox.vm.agent.net.rx_errors",  description="Guest NIC RX errors",       unit="{error}")
        self._vm_agent_net_tx_errors  = m.create_gauge("proxmox.vm.agent.net.tx_errors",  description="Guest NIC TX errors",       unit="{error}")
        self._vm_agent_net_rx_dropped = m.create_gauge("proxmox.vm.agent.net.rx_dropped", description="Guest NIC RX dropped",      unit="{packet}")
        self._vm_agent_net_tx_dropped = m.create_gauge("proxmox.vm.agent.net.tx_dropped", description="Guest NIC TX dropped",      unit="{packet}")

        # -- LXC live
        self._lxc_status       = m.create_gauge("proxmox.lxc.status",       description="LXC running (1=running)",         unit="1")
        self._lxc_cpu_usage    = m.create_gauge("proxmox.lxc.cpu.usage",    description="LXC CPU utilization",             unit="%")
        self._lxc_cpu_count    = m.create_gauge("proxmox.lxc.cpu.count",    description="LXC vCPU count",                  unit="{cpu}")
        self._lxc_memory_used  = m.create_gauge("proxmox.lxc.memory.used",  description="LXC memory used",                 unit="By")
        self._lxc_memory_total = m.create_gauge("proxmox.lxc.memory.total", description="LXC memory total",                unit="By")
        self._lxc_swap_used    = m.create_gauge("proxmox.lxc.swap.used",    description="LXC swap used",                   unit="By")
        self._lxc_swap_total   = m.create_gauge("proxmox.lxc.swap.total",   description="LXC swap total",                  unit="By")
        self._lxc_disk_read    = m.create_gauge("proxmox.lxc.disk.read",    description="LXC disk bytes read",             unit="By")
        self._lxc_disk_write   = m.create_gauge("proxmox.lxc.disk.write",   description="LXC disk bytes written",          unit="By")
        self._lxc_disk_size    = m.create_gauge("proxmox.lxc.disk.size",    description="LXC primary disk size",           unit="By")
        self._lxc_network_in   = m.create_gauge("proxmox.lxc.network.in",   description="LXC network bytes in",            unit="By")
        self._lxc_network_out  = m.create_gauge("proxmox.lxc.network.out",  description="LXC network bytes out",           unit="By")

        # -- LXC config
        self._lxc_config_cores       = m.create_gauge("proxmox.lxc.config.cores",       description="LXC configured cores",        unit="{core}")
        self._lxc_config_memory_mib  = m.create_gauge("proxmox.lxc.config.memory_mib",  description="LXC configured memory",       unit="MiBy")
        self._lxc_config_swap_mib    = m.create_gauge("proxmox.lxc.config.swap_mib",    description="LXC configured swap",         unit="MiBy")
        self._lxc_config_cpulimit    = m.create_gauge("proxmox.lxc.config.cpulimit",    description="LXC CPU limit",               unit="1")
        self._lxc_config_cpuunits    = m.create_gauge("proxmox.lxc.config.cpuunits",    description="LXC CPU units (weight)",      unit="1")
        self._lxc_config_onboot      = m.create_gauge("proxmox.lxc.config.onboot",      description="LXC start on boot (1=yes)",   unit="1")
        self._lxc_config_unprivileged = m.create_gauge("proxmox.lxc.config.unprivileged", description="LXC unprivileged (1=yes)",  unit="1")

        # -- LXC rrddata
        self._lxc_disk_used          = m.create_gauge("proxmox.lxc.disk.used",          description="LXC disk used (from rrd)",    unit="By")
        self._lxc_memory_host        = m.create_gauge("proxmox.lxc.memory.host",        description="LXC host memory used",        unit="By")
        self._lxc_pressure_cpu_some  = m.create_gauge("proxmox.lxc.pressure.cpu.some",  description="LXC CPU pressure (some)",     unit="%")
        self._lxc_pressure_cpu_full  = m.create_gauge("proxmox.lxc.pressure.cpu.full",  description="LXC CPU pressure (full)",     unit="%")
        self._lxc_pressure_io_some   = m.create_gauge("proxmox.lxc.pressure.io.some",   description="LXC IO pressure (some)",      unit="%")
        self._lxc_pressure_mem_some  = m.create_gauge("proxmox.lxc.pressure.memory.some", description="LXC memory pressure (some)", unit="%")
        self._lxc_pressure_mem_full  = m.create_gauge("proxmox.lxc.pressure.memory.full", description="LXC memory pressure (full)", unit="%")

        # -- LXC snapshots
        self._lxc_snapshot_count     = m.create_gauge("proxmox.lxc.snapshot.count",     description="LXC snapshot count",          unit="{snapshot}")

        # -- Storage
        self._storage_used         = m.create_gauge("proxmox.storage.used",         description="Storage bytes used",          unit="By")
        self._storage_total        = m.create_gauge("proxmox.storage.total",        description="Storage bytes total",         unit="By")
        self._storage_avail        = m.create_gauge("proxmox.storage.avail",        description="Storage bytes available",     unit="By")
        self._storage_enabled      = m.create_gauge("proxmox.storage.enabled",      description="Storage enabled (1=yes)",     unit="1")
        self._storage_active       = m.create_gauge("proxmox.storage.active",       description="Storage active/mounted (1=yes)", unit="1")
        self._storage_backup_count = m.create_gauge("proxmox.storage.backup_count", description="Backup files in storage",     unit="{backup}")

    # ------------------------------------------------------------------ public

    def collect(self):
        """Run a full collection cycle."""
        cluster_name = self._collect_cluster()
        self._collect_ha(cluster_name)
        self._collect_replication(cluster_name)
        self._collect_backups(cluster_name)
        self._collect_ceph(cluster_name)
        self._collect_nodes(cluster_name)

    # ------------------------------------------------------------------ Cluster

    def _collect_cluster(self) -> str:
        cluster_status = self._client.get_cluster_status()
        cluster_name = "proxmox"
        nodes_total = 0
        nodes_online = 0

        for item in cluster_status:
            if item.get("type") == "cluster":
                cluster_name = item.get("name", "proxmox")
            elif item.get("type") == "node":
                nodes_total += 1
                if item.get("online", 0):
                    nodes_online += 1

        dims = {"cluster_name": cluster_name}
        self._set(self._cluster_nodes_total, nodes_total, attributes=dims)
        self._set(self._cluster_nodes_online, nodes_online, attributes=dims)
        logger.debug("Cluster %s: %d/%d nodes online", cluster_name, nodes_online, nodes_total)
        return cluster_name

    # ------------------------------------------------------------------ HA

    def _collect_ha(self, cluster_name: str):
        try:
            dims = {"cluster_name": cluster_name}
            quorate = 0
            for item in self._client.get_ha_status():
                if item.get("type") == "quorum":
                    quorate = 1 if item.get("quorate", 0) else 0
            self._set(self._cluster_ha_quorate, quorate, attributes=dims)

            for resource in self._client.get_ha_resources():
                sid = resource.get("sid", "unknown")
                state = resource.get("state", "")
                rdims = {"ha_resource": sid, "cluster_name": cluster_name}
                self._set(self._cluster_ha_resource_running, 1 if state == "started" else 0, attributes=rdims)
        except Exception:
            logger.exception("Error collecting HA metrics")

    # ------------------------------------------------------------------ Replication

    def _collect_replication(self, cluster_name: str):
        try:
            for job in self._client.get_replication_jobs():
                job_id = job.get("id", "unknown")
                rdims = {"replication_job": job_id, "cluster_name": cluster_name}
                self._set(self._cluster_replication_fail_count, job.get("fail_count", 0), attributes=rdims)
                duration = job.get("duration", 0)
                if duration:
                    self._set(self._cluster_replication_duration, duration, attributes=rdims)
                self._set(self._cluster_replication_error, 1 if job.get("error") else 0, attributes=rdims)
        except Exception:
            logger.exception("Error collecting replication metrics")

    # ------------------------------------------------------------------ Backups

    def _collect_backups(self, cluster_name: str):
        try:
            dims = {"cluster_name": cluster_name}
            self._set(self._cluster_backup_unprotected_guests, 
                len(self._client.get_not_backed_up()), attributes=dims
            )
        except Exception:
            logger.exception("Error collecting backup metrics")

    # ------------------------------------------------------------------ Ceph

    def _collect_ceph(self, cluster_name: str):
        try:
            status = self._client.get_ceph_status()
            dims = {"cluster_name": cluster_name}

            health_str = status.get("health", {}).get("status", "HEALTH_UNKNOWN")
            health_val = {"HEALTH_OK": 2, "HEALTH_WARN": 1, "HEALTH_ERR": 0}.get(health_str, -1)
            self._set(self._ceph_health, health_val, attributes=dims)

            osdmap = status.get("osdmap", {})
            self._set(self._ceph_osd_total, osdmap.get("num_osds", 0), attributes=dims)
            self._set(self._ceph_osd_up, osdmap.get("num_up_osds", 0), attributes=dims)
            self._set(self._ceph_osd_in, osdmap.get("num_in_osds", 0), attributes=dims)

            pgmap = status.get("pgmap", {})
            self._set(self._ceph_pg_total, pgmap.get("num_pgs", 0), attributes=dims)
            self._set(self._ceph_bytes_used, pgmap.get("bytes_used", 0), attributes=dims)
            self._set(self._ceph_bytes_avail, pgmap.get("bytes_avail", 0), attributes=dims)
            self._set(self._ceph_bytes_total, pgmap.get("bytes_total", 0), attributes=dims)
            self._set(self._ceph_io_read_bps, pgmap.get("read_bytes_sec", 0), attributes=dims)
            self._set(self._ceph_io_write_bps, pgmap.get("write_bytes_sec", 0), attributes=dims)
            self._set(self._ceph_io_recovering_bps, pgmap.get("recovering_bytes_per_sec", 0), attributes=dims)

            monmap = status.get("monmap", {})
            self._set(self._ceph_mon_count, monmap.get("num_mons", 0), attributes=dims)

            try:
                for flag in self._client.get_ceph_flags():
                    name = flag.get("name", "")
                    if name in ("noout", "noin", "nodown", "pause", "full", "nearfull"):
                        fdims = {"ceph_flag": name, "cluster_name": cluster_name}
                        self._set(self._ceph_flag, 1 if flag.get("value") else 0, attributes=fdims)
            except Exception:
                logger.debug("Ceph flags not available")

        except Exception:
            logger.debug("Ceph not configured or not accessible — skipping")

    # ------------------------------------------------------------------ Nodes

    def _collect_nodes(self, cluster_name: str):
        for node_summary in self._client.get_nodes():
            node = node_summary.get("node")
            if not node:
                continue
            try:
                status = self._client.get_node_status(node)
                dims = {"node_name": node, "cluster_name": cluster_name}

                # CPU
                self._set(self._node_cpu_usage, status.get("cpu", 0) * 100, attributes=dims)
                cpuinfo = status.get("cpuinfo", {})
                self._set(self._node_cpu_count, cpuinfo.get("cpus", 0), attributes=dims)
                self._set(self._node_cpu_sockets, cpuinfo.get("sockets", 0), attributes=dims)

                # Load average
                loadavg = status.get("loadavg", [0, 0, 0])
                self._set(self._node_loadavg_1m, float(loadavg[0]) if len(loadavg) > 0 else 0, attributes=dims)
                self._set(self._node_loadavg_5m, float(loadavg[1]) if len(loadavg) > 1 else 0, attributes=dims)
                self._set(self._node_loadavg_15m, float(loadavg[2]) if len(loadavg) > 2 else 0, attributes=dims)

                # Memory
                mem = status.get("memory", {})
                self._set(self._node_memory_used, mem.get("used", 0), attributes=dims)
                self._set(self._node_memory_total, mem.get("total", 0), attributes=dims)
                self._set(self._node_memory_free, mem.get("free", 0), attributes=dims)

                # Swap
                swap = status.get("swap", {})
                self._set(self._node_swap_used, swap.get("used", 0), attributes=dims)
                self._set(self._node_swap_total, swap.get("total", 0), attributes=dims)
                self._set(self._node_swap_free, swap.get("free", 0), attributes=dims)

                # Root filesystem
                disk = status.get("rootfs", {})
                self._set(self._node_disk_used, disk.get("used", 0), attributes=dims)
                self._set(self._node_disk_total, disk.get("total", 0), attributes=dims)
                self._set(self._node_disk_avail, disk.get("avail", 0), attributes=dims)

                # Network (aggregate)
                self._set(self._node_network_in, node_summary.get("netin", 0), attributes=dims)
                self._set(self._node_network_out, node_summary.get("netout", 0), attributes=dims)

                # Uptime / KSM
                self._set(self._node_uptime, status.get("uptime", 0), attributes=dims)
                ksm = status.get("ksm", {})
                if ksm:
                    self._set(self._node_ksm_shared, ksm.get("shared", 0), attributes=dims)

                logger.debug("Collected live metrics for node %s", node)

                # RRD — metrics only available here
                self._collect_node_rrddata(node, cluster_name)

                # Services / subscription / updates / tasks / netstat
                self._collect_node_services(node, cluster_name)
                self._collect_node_subscription(node, cluster_name)
                self._collect_node_updates(node, cluster_name)
                self._collect_node_netstat(node, cluster_name)
                self._collect_node_tasks(node, cluster_name)

                # Physical disks + SMART
                self._collect_disks(node, cluster_name)

                # VMs / LXC / Storage
                self._collect_vms(node, cluster_name)
                self._collect_containers(node, cluster_name)
                self._collect_storage(node, cluster_name)

            except Exception:
                logger.exception("Error collecting metrics for node %s", node)

    def _collect_node_rrddata(self, node: str, cluster_name: str):
        try:
            rrd = self._client.get_node_rrddata(node)
            if not rrd:
                return
            dims = {"node_name": node, "cluster_name": cluster_name}

            iowait = _latest(rrd, "iowait")
            if iowait is not None:
                self._set(self._node_cpu_iowait, iowait * 100, attributes=dims)

            memavail = _latest(rrd, "memavailable")
            if memavail is not None:
                self._set(self._node_memory_available, memavail, attributes=dims)

            arcsize = _latest(rrd, "arcsize")
            if arcsize is not None:
                self._set(self._node_zfs_arcsize, arcsize, attributes=dims)

            for field, gauge in (
                ("pressurecpusome",    self._node_pressure_cpu_some),
                ("pressureiosome",     self._node_pressure_io_some),
                ("pressureiofull",     self._node_pressure_io_full),
                ("pressurememorysome", self._node_pressure_mem_some),
                ("pressurememoryfull", self._node_pressure_mem_full),
            ):
                val = _latest(rrd, field)
                if val is not None:
                    gauge.set(val * 100, attributes=dims)
        except Exception:
            logger.debug("Could not collect rrddata for node %s", node)

    def _collect_node_services(self, node: str, cluster_name: str):
        try:
            for svc in self._client.get_node_services(node):
                name = svc.get("service", svc.get("name", ""))
                if name not in MONITORED_SERVICES:
                    continue
                active = 1 if svc.get("active-state", svc.get("state", "")) == "active" else 0
                sdims = {"service_name": name, "node_name": node, "cluster_name": cluster_name}
                self._set(self._node_service_status, active, attributes=sdims)
        except Exception:
            logger.exception("Error collecting service metrics for node %s", node)

    def _collect_node_subscription(self, node: str, cluster_name: str):
        try:
            sub = self._client.get_node_subscription(node)
            active = 1 if sub.get("status", "notfound") == "active" else 0
            dims = {"node_name": node, "cluster_name": cluster_name}
            self._set(self._node_subscription_active, active, attributes=dims)
        except Exception:
            logger.debug("Could not collect subscription for node %s", node)

    def _collect_node_updates(self, node: str, cluster_name: str):
        try:
            updates = self._client.get_node_apt_updates(node)
            proxmox_updates = sum(1 for u in updates if "proxmox" in u.get("Origin", "").lower())
            dims = {"node_name": node, "cluster_name": cluster_name}
            self._set(self._node_updates_pending, len(updates), attributes=dims)
            self._set(self._node_updates_proxmox_pending, proxmox_updates, attributes=dims)
        except Exception:
            logger.debug("Could not collect apt updates for node %s", node)

    def _collect_node_netstat(self, node: str, cluster_name: str):
        try:
            for iface in self._client.get_node_netstat(node):
                vmid = str(iface.get("vmid", ""))
                dev = iface.get("dev", "")
                if not vmid or not dev:
                    continue
                idims = {"vmid": vmid, "iface": dev, "node_name": node, "cluster_name": cluster_name}
                self._set(self._node_netstat_in, iface.get("in", 0), attributes=idims)
                self._set(self._node_netstat_out, iface.get("out", 0), attributes=idims)
        except Exception:
            logger.debug("Could not collect netstat for node %s", node)

    def _collect_node_tasks(self, node: str, cluster_name: str):
        try:
            tasks = self._client.get_node_tasks(node)
            error_count = sum(1 for t in tasks if t.get("status", "").startswith("FAILED"))
            running_count = sum(1 for t in tasks if not t.get("endtime"))
            dims = {"node_name": node, "cluster_name": cluster_name}
            self._set(self._node_tasks_errors, error_count, attributes=dims)
            self._set(self._node_tasks_running, running_count, attributes=dims)
        except Exception:
            logger.debug("Could not collect tasks for node %s", node)

    # ------------------------------------------------------------------ Physical Disks

    def _collect_disks(self, node: str, cluster_name: str):
        try:
            for disk in self._client.get_node_disks(node):
                dev = disk.get("devpath", disk.get("dev", ""))
                if not dev:
                    continue
                disk_name = dev.replace("/dev/", "")
                dims = {"disk_dev": disk_name, "node_name": node, "cluster_name": cluster_name}
                self._set(self._node_disk_device_size, disk.get("size", 0), attributes=dims)
                health = disk.get("health", "")
                health_val = 1 if health.upper() == "PASSED" else (0 if health.upper() == "FAILED" else -1)
                self._set(self._node_disk_device_smart, health_val, attributes=dims)
                self._collect_disk_smart(node, dev, disk_name, cluster_name)
        except Exception:
            logger.exception("Error collecting disk list for node %s", node)

    def _collect_disk_smart(self, node: str, dev: str, disk_name: str, cluster_name: str):
        try:
            smart = self._client.get_disk_smart(node, dev)
            dims = {"disk_dev": disk_name, "node_name": node, "cluster_name": cluster_name}
            SMART_IDS = {
                5:   "reallocated_sectors",
                9:   "power_on_hours",
                187: "uncorrectable_errors",
                188: "command_timeout",
                197: "pending_sectors",
                198: "uncorrectable_sector_count",
                199: "udma_crc_errors",
            }
            for attr in smart.get("attributes", []):
                attr_id = attr.get("id")
                if attr_id in SMART_IDS:
                    adims = {**dims, "smart_attr": SMART_IDS[attr_id]}
                    raw = attr.get("raw", {})
                    raw_val = raw.get("value", 0) if isinstance(raw, dict) else int(raw or 0)
                    self._set(self._node_disk_device_smart_attr, raw_val, attributes=adims)
        except Exception:
            logger.debug("SMART data not available for %s on %s", dev, node)

    # ------------------------------------------------------------------ VMs

    def _collect_vms(self, node: str, cluster_name: str):
        for vm in self._client.get_vms(node):
            vmid = vm.get("vmid")
            name = vm.get("name", str(vmid))
            if not vmid:
                continue
            try:
                status = self._client.get_vm_status(node, vmid)
                dims = {"vmid": str(vmid), "vm_name": name, "node_name": node, "cluster_name": cluster_name}

                vm_running = 1 if status.get("status") == "running" else 0
                self._set(self._vm_status, vm_running, attributes=dims)

                self._set(self._vm_cpu_usage, status.get("cpu", 0) * 100, attributes=dims)
                self._set(self._vm_cpu_count, status.get("cpus", 0), attributes=dims)

                self._set(self._vm_memory_used, status.get("mem", 0), attributes=dims)
                self._set(self._vm_memory_total, status.get("maxmem", 0), attributes=dims)

                balloon = status.get("ballooninfo", {})
                if balloon:
                    self._set(self._vm_balloon_current, balloon.get("current_allocated", 0), attributes=dims)
                    self._set(self._vm_balloon_target, balloon.get("target_allocated", 0), attributes=dims)

                self._set(self._vm_disk_read, status.get("diskread", 0), attributes=dims)
                self._set(self._vm_disk_write, status.get("diskwrite", 0), attributes=dims)
                self._set(self._vm_disk_size, status.get("maxdisk", 0), attributes=dims)

                self._set(self._vm_network_in, status.get("netin", 0), attributes=dims)
                self._set(self._vm_network_out, status.get("netout", 0), attributes=dims)

                self._set(self._vm_uptime, status.get("uptime", 0), attributes=dims)

                self._collect_vm_config(node, vmid, name, cluster_name)

                if vm_running:
                    self._collect_vm_rrddata(node, vmid, name, cluster_name)

                self._collect_vm_snapshots(node, vmid, name, cluster_name)

                if vm_running and status.get("agent", 0):
                    self._collect_vm_agent(node, vmid, name, cluster_name)

                logger.debug("Collected metrics for VM %s (%s) on %s", vmid, name, node)

            except Exception:
                logger.exception("Error collecting metrics for VM %s on %s", vmid, node)

    def _collect_vm_config(self, node: str, vmid: int, vm_name: str, cluster_name: str):
        try:
            cfg = self._client.get_vm_config(node, vmid)
            dims = {"vmid": str(vmid), "vm_name": vm_name, "node_name": node, "cluster_name": cluster_name}
            self._set(self._vm_config_cores, cfg.get("cores", 1), attributes=dims)
            self._set(self._vm_config_sockets, cfg.get("sockets", 1), attributes=dims)
            self._set(self._vm_config_memory_mib, cfg.get("memory", 0), attributes=dims)
            self._set(self._vm_config_balloon_mib, cfg.get("balloon", 0), attributes=dims)
            self._set(self._vm_config_cpulimit, cfg.get("cpulimit", 0), attributes=dims)
            self._set(self._vm_config_cpuunits, cfg.get("cpuunits", 1024), attributes=dims)
            self._set(self._vm_config_onboot, 1 if cfg.get("onboot", 0) else 0, attributes=dims)
        except Exception:
            logger.debug("Could not collect config for VM %s on %s", vmid, node)

    def _collect_vm_rrddata(self, node: str, vmid: int, vm_name: str, cluster_name: str):
        try:
            rrd = self._client.get_vm_rrddata(node, vmid)
            if not rrd:
                return
            dims = {"vmid": str(vmid), "vm_name": vm_name, "node_name": node, "cluster_name": cluster_name}

            disk_used = _latest(rrd, "disk")
            if disk_used is not None:
                self._set(self._vm_disk_used, disk_used, attributes=dims)

            memhost = _latest(rrd, "memhost")
            if memhost is not None:
                self._set(self._vm_memory_host, memhost, attributes=dims)

            for field, gauge in (
                ("pressurecpusome",    self._vm_pressure_cpu_some),
                ("pressurecpufull",    self._vm_pressure_cpu_full),
                ("pressureiosome",     self._vm_pressure_io_some),
                ("pressurememorysome", self._vm_pressure_mem_some),
                ("pressurememoryfull", self._vm_pressure_mem_full),
            ):
                val = _latest(rrd, field)
                if val is not None:
                    gauge.set(val * 100, attributes=dims)
        except Exception:
            logger.debug("Could not collect rrddata for VM %s on %s", vmid, node)

    def _collect_vm_snapshots(self, node: str, vmid: int, vm_name: str, cluster_name: str):
        try:
            snaps = self._client.get_vm_snapshots(node, vmid)
            real_snaps = [s for s in snaps if s.get("name") != "current"]
            dims = {"vmid": str(vmid), "vm_name": vm_name, "node_name": node, "cluster_name": cluster_name}
            self._set(self._vm_snapshot_count, len(real_snaps), attributes=dims)
        except Exception:
            logger.debug("Could not collect snapshots for VM %s on %s", vmid, node)

    def _collect_vm_agent(self, node: str, vmid: int, vm_name: str, cluster_name: str):
        try:
            for fs in self._client.get_vm_agent_fsinfo(node, vmid):
                mp = fs.get("mountpoint", "")
                if not mp:
                    continue
                fdims = {"vmid": str(vmid), "vm_name": vm_name, "mountpoint": mp, "node_name": node, "cluster_name": cluster_name}
                self._set(self._vm_agent_disk_used, fs.get("used-bytes", 0), attributes=fdims)
                self._set(self._vm_agent_disk_total, fs.get("total-bytes", 0), attributes=fdims)
        except Exception:
            logger.debug("Guest agent fsinfo not available for VM %s", vmid)

        try:
            for iface in self._client.get_vm_agent_network(node, vmid):
                iface_name = iface.get("name", "")
                if not iface_name or iface_name == "lo":
                    continue
                stats = iface.get("statistics", {})
                if not stats:
                    continue
                idims = {"vmid": str(vmid), "vm_name": vm_name, "iface": iface_name, "node_name": node, "cluster_name": cluster_name}
                self._set(self._vm_agent_net_rx_bytes, stats.get("rx-bytes", 0), attributes=idims)
                self._set(self._vm_agent_net_tx_bytes, stats.get("tx-bytes", 0), attributes=idims)
                self._set(self._vm_agent_net_rx_errors, stats.get("rx-errs", 0), attributes=idims)
                self._set(self._vm_agent_net_tx_errors, stats.get("tx-errs", 0), attributes=idims)
                self._set(self._vm_agent_net_rx_dropped, stats.get("rx-dropped", 0), attributes=idims)
                self._set(self._vm_agent_net_tx_dropped, stats.get("tx-dropped", 0), attributes=idims)
        except Exception:
            logger.debug("Guest agent network info not available for VM %s", vmid)

    # ------------------------------------------------------------------ LXC

    def _collect_containers(self, node: str, cluster_name: str):
        for ct in self._client.get_containers(node):
            vmid = ct.get("vmid")
            name = ct.get("name", str(vmid))
            if not vmid:
                continue
            try:
                status = self._client.get_container_status(node, vmid)
                dims = {"vmid": str(vmid), "lxc_name": name, "node_name": node, "cluster_name": cluster_name}

                ct_running = 1 if status.get("status") == "running" else 0
                self._set(self._lxc_status, ct_running, attributes=dims)

                self._set(self._lxc_cpu_usage, status.get("cpu", 0) * 100, attributes=dims)
                self._set(self._lxc_cpu_count, status.get("cpus", 0), attributes=dims)

                self._set(self._lxc_memory_used, status.get("mem", 0), attributes=dims)
                self._set(self._lxc_memory_total, status.get("maxmem", 0), attributes=dims)

                self._set(self._lxc_swap_used, status.get("swap", 0), attributes=dims)
                self._set(self._lxc_swap_total, status.get("maxswap", 0), attributes=dims)

                self._set(self._lxc_disk_read, status.get("diskread", 0), attributes=dims)
                self._set(self._lxc_disk_write, status.get("diskwrite", 0), attributes=dims)
                self._set(self._lxc_disk_size, status.get("maxdisk", 0), attributes=dims)

                self._set(self._lxc_network_in, status.get("netin", 0), attributes=dims)
                self._set(self._lxc_network_out, status.get("netout", 0), attributes=dims)

                self._collect_ct_config(node, vmid, name, cluster_name)

                if ct_running:
                    self._collect_ct_rrddata(node, vmid, name, cluster_name)

                self._collect_ct_snapshots(node, vmid, name, cluster_name)

                logger.debug("Collected metrics for LXC %s (%s) on %s", vmid, name, node)

            except Exception:
                logger.exception("Error collecting metrics for LXC %s on %s", vmid, node)

    def _collect_ct_config(self, node: str, vmid: int, lxc_name: str, cluster_name: str):
        try:
            cfg = self._client.get_container_config(node, vmid)
            dims = {"vmid": str(vmid), "lxc_name": lxc_name, "node_name": node, "cluster_name": cluster_name}
            self._set(self._lxc_config_cores, cfg.get("cores", 1), attributes=dims)
            self._set(self._lxc_config_memory_mib, cfg.get("memory", 0), attributes=dims)
            self._set(self._lxc_config_swap_mib, cfg.get("swap", 0), attributes=dims)
            self._set(self._lxc_config_cpulimit, cfg.get("cpulimit", 0), attributes=dims)
            self._set(self._lxc_config_cpuunits, cfg.get("cpuunits", 1024), attributes=dims)
            self._set(self._lxc_config_onboot, 1 if cfg.get("onboot", 0) else 0, attributes=dims)
            self._set(self._lxc_config_unprivileged, 1 if cfg.get("unprivileged", 0) else 0, attributes=dims)
        except Exception:
            logger.debug("Could not collect config for LXC %s on %s", vmid, node)

    def _collect_ct_rrddata(self, node: str, vmid: int, lxc_name: str, cluster_name: str):
        try:
            rrd = self._client.get_container_rrddata(node, vmid)
            if not rrd:
                return
            dims = {"vmid": str(vmid), "lxc_name": lxc_name, "node_name": node, "cluster_name": cluster_name}

            disk_used = _latest(rrd, "disk")
            if disk_used is not None:
                self._set(self._lxc_disk_used, disk_used, attributes=dims)

            memhost = _latest(rrd, "memhost")
            if memhost is not None:
                self._set(self._lxc_memory_host, memhost, attributes=dims)

            for field, gauge in (
                ("pressurecpusome",    self._lxc_pressure_cpu_some),
                ("pressurecpufull",    self._lxc_pressure_cpu_full),
                ("pressureiosome",     self._lxc_pressure_io_some),
                ("pressurememorysome", self._lxc_pressure_mem_some),
                ("pressurememoryfull", self._lxc_pressure_mem_full),
            ):
                val = _latest(rrd, field)
                if val is not None:
                    gauge.set(val * 100, attributes=dims)
        except Exception:
            logger.debug("Could not collect rrddata for LXC %s on %s", vmid, node)

    def _collect_ct_snapshots(self, node: str, vmid: int, lxc_name: str, cluster_name: str):
        try:
            snaps = self._client.get_container_snapshots(node, vmid)
            real_snaps = [s for s in snaps if s.get("name") != "current"]
            dims = {"vmid": str(vmid), "lxc_name": lxc_name, "node_name": node, "cluster_name": cluster_name}
            self._set(self._lxc_snapshot_count, len(real_snaps), attributes=dims)
        except Exception:
            logger.debug("Could not collect snapshots for LXC %s on %s", vmid, node)

    # ------------------------------------------------------------------ Storage

    def _collect_storage(self, node: str, cluster_name: str):
        for storage in self._client.get_storage(node):
            name = storage.get("storage")
            if not name:
                continue
            dims = {"storage_name": name, "node_name": node, "cluster_name": cluster_name}

            self._set(self._storage_used, storage.get("used", 0), attributes=dims)
            self._set(self._storage_total, storage.get("total", 0), attributes=dims)
            self._set(self._storage_avail, storage.get("avail", 0), attributes=dims)
            self._set(self._storage_enabled, 1 if storage.get("enabled", 1) else 0, attributes=dims)
            self._set(self._storage_active, 1 if storage.get("active", 0) else 0, attributes=dims)

            self._collect_storage_backups(node, name, cluster_name)

    def _collect_storage_backups(self, node: str, storage: str, cluster_name: str):
        try:
            content = self._client.get_storage_content(node, storage)
            backup_count = sum(1 for c in content if "backup" in str(c.get("volid", "")) or c.get("content") == "backup")
            dims = {"storage_name": storage, "node_name": node, "cluster_name": cluster_name}
            self._set(self._storage_backup_count, backup_count, attributes=dims)
        except Exception:
            logger.debug("Could not collect content for storage %s on %s", storage, node)
