# proxmox-bindplane-source

A standalone OpenTelemetry collector for Proxmox VE that pushes metrics via OTLP gRPC to BindPlane OP, which in turn routes them to Dynatrace (or any other OTLP destination).

This is **not** a Dynatrace Extension 2.0. It is a plain Python service that runs anywhere Docker runs.

---

## Architecture

```
┌─────────────────────┐        OTLP/gRPC       ┌──────────────────┐      ┌───────────────┐
│  Proxmox VE cluster │  ←── REST API (proxmoxer) ──  proxmox-     │ ───► │  BindPlane OP │ ───► Dynatrace
│  (8006/tcp)         │                          │  collector       │      │  :4317 gRPC   │
└─────────────────────┘                          │  (this service)  │      └──────────────-┘
                                                 └──────────────────┘
```

**Flow:**
1. `proxmox-collector` polls the Proxmox REST API every `COLLECTION_INTERVAL_SECONDS` seconds.
2. Every metric is recorded as an OpenTelemetry synchronous Gauge.
3. The OTel SDK's `PeriodicExportingMetricReader` pushes a batch to BindPlane via OTLP gRPC.
4. BindPlane applies optional processors (e.g. metric renaming) and forwards to Dynatrace.

---

## Configuration

Config is loaded from `config.yaml` first, then env vars override individual fields.

| YAML key | Env var | Default | Description |
|---|---|---|---|
| `proxmox.host` | `PROXMOX_HOST` | `192.168.1.1` | Proxmox VE host/IP |
| `proxmox.port` | `PROXMOX_PORT` | `8006` | Proxmox API port |
| `proxmox.username` | `PROXMOX_USERNAME` | `monitoring@pam` | API token user |
| `proxmox.token_name` | `PROXMOX_TOKEN_NAME` | `dynatrace` | API token name |
| `proxmox.token_value` | `PROXMOX_TOKEN_VALUE` | *(required)* | API token secret |
| `proxmox.verify_ssl` | `PROXMOX_VERIFY_SSL` | `false` | Verify Proxmox TLS cert |
| `otlp.endpoint` | `OTLP_ENDPOINT` | `http://192.168.1.190:4317` | BindPlane OTLP endpoint |
| `otlp.insecure` | `OTLP_INSECURE` | `true` | Skip TLS for OTLP |
| `otlp.headers` | `OTLP_HEADERS` | `{}` | Extra OTLP headers (JSON string in env) |
| `collector.interval_seconds` | `COLLECTION_INTERVAL_SECONDS` | `60` | Poll interval |
| `collector.service_name` | `SERVICE_NAME` | `proxmox-collector` | OTel service.name |
| `collector.service_version` | `SERVICE_VERSION` | `1.0.0` | OTel service.version |

Additional env var: `LOG_LEVEL` (default `INFO`), `CONFIG_PATH` (default `config.yaml`).

---

## Docker Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/faultylogic/proxmox-bindplane-source.git
cd proxmox-bindplane-source

# 2. Edit config.yaml or export env vars
export PROXMOX_HOST=192.168.1.X
export PROXMOX_TOKEN_VALUE=your-actual-token-secret

# 3. Build and run
docker compose up -d

# 4. Tail logs
docker compose logs -f
```

### Proxmox API Token setup

```bash
# On the Proxmox node (as root)
pveum user add monitoring@pam
pveum aclmod / -user monitoring@pam -role PVEAuditor
pveum user token add monitoring@pam dynatrace --privsep=0
# Copy the token secret printed by the last command → PROXMOX_TOKEN_VALUE
```

---

## BindPlane Pipeline Setup

BindPlane already exposes an OTLP gRPC receiver on port 4317 by default. Simply point the collector at it:

```
OTLP_ENDPOINT=http://192.168.1.190:4317
```

To route the data to Dynatrace, create a pipeline in BindPlane:

1. **Source**: OTLP (already listening on 4317)
2. **Processor** *(optional)*: `metricstransform` to rename `proxmox.*` → `custom.proxmox.*`
3. **Destination**: Dynatrace — provide your tenant URL and `metrics.ingest` API token

See [`bindplane/pipeline-example.yaml`](bindplane/pipeline-example.yaml) for a full reference.

---

## Metric Names: this collector vs. the Dynatrace Extension

| Layer | Metric name example |
|---|---|
| This collector (OTel) | `proxmox.node.cpu.usage` |
| After BindPlane rename processor | `custom.proxmox.node.cpu.usage` |
| Dynatrace Extension 2.0 (native) | `custom.proxmox.node.cpu.usage` |

If your Dynatrace environment already has dashboards/alerts built against `custom.proxmox.*` from the Extension, add the `metricstransform` rename processor in BindPlane so the metric names align without touching any Dynatrace config.

---

## Full Metrics Reference

### Cluster

| Metric | Description | Attributes |
|---|---|---|
| `proxmox.cluster.nodes.total` | Total nodes | `cluster_name` |
| `proxmox.cluster.nodes.online` | Online nodes | `cluster_name` |
| `proxmox.cluster.ha.quorate` | Quorum (1=quorate) | `cluster_name` |
| `proxmox.cluster.ha.resource.running` | HA resource running | `cluster_name`, `ha_resource` |
| `proxmox.cluster.replication.fail_count` | Replication failures | `cluster_name`, `replication_job` |
| `proxmox.cluster.replication.duration` | Replication duration (s) | `cluster_name`, `replication_job` |
| `proxmox.cluster.replication.error` | Replication error flag | `cluster_name`, `replication_job` |
| `proxmox.cluster.backup.unprotected_guests` | Guests with no backup job | `cluster_name` |

### Ceph

| Metric | Description | Attributes |
|---|---|---|
| `proxmox.ceph.health` | Health (2=OK,1=WARN,0=ERR) | `cluster_name` |
| `proxmox.ceph.osd.total` | Total OSDs | `cluster_name` |
| `proxmox.ceph.osd.up` | Up OSDs | `cluster_name` |
| `proxmox.ceph.osd.in` | In OSDs | `cluster_name` |
| `proxmox.ceph.pg.total` | Placement groups | `cluster_name` |
| `proxmox.ceph.bytes.used` | Bytes used | `cluster_name` |
| `proxmox.ceph.bytes.avail` | Bytes available | `cluster_name` |
| `proxmox.ceph.bytes.total` | Bytes total | `cluster_name` |
| `proxmox.ceph.io.read_bps` | Read throughput | `cluster_name` |
| `proxmox.ceph.io.write_bps` | Write throughput | `cluster_name` |
| `proxmox.ceph.io.recovering_bps` | Recovery throughput | `cluster_name` |
| `proxmox.ceph.mon.count` | Monitor count | `cluster_name` |
| `proxmox.ceph.flag` | Ceph flag active | `cluster_name`, `ceph_flag` |

### Node — live

| Metric | Unit | Attributes |
|---|---|---|
| `proxmox.node.cpu.usage` | % | `node_name`, `cluster_name` |
| `proxmox.node.cpu.count` | {cpu} | `node_name`, `cluster_name` |
| `proxmox.node.cpu.sockets` | {socket} | `node_name`, `cluster_name` |
| `proxmox.node.loadavg.1m` | 1 | `node_name`, `cluster_name` |
| `proxmox.node.loadavg.5m` | 1 | `node_name`, `cluster_name` |
| `proxmox.node.loadavg.15m` | 1 | `node_name`, `cluster_name` |
| `proxmox.node.memory.used` | By | `node_name`, `cluster_name` |
| `proxmox.node.memory.total` | By | `node_name`, `cluster_name` |
| `proxmox.node.memory.free` | By | `node_name`, `cluster_name` |
| `proxmox.node.swap.used` | By | `node_name`, `cluster_name` |
| `proxmox.node.swap.total` | By | `node_name`, `cluster_name` |
| `proxmox.node.swap.free` | By | `node_name`, `cluster_name` |
| `proxmox.node.disk.used` | By | `node_name`, `cluster_name` |
| `proxmox.node.disk.total` | By | `node_name`, `cluster_name` |
| `proxmox.node.disk.avail` | By | `node_name`, `cluster_name` |
| `proxmox.node.network.in` | By | `node_name`, `cluster_name` |
| `proxmox.node.network.out` | By | `node_name`, `cluster_name` |
| `proxmox.node.uptime` | s | `node_name`, `cluster_name` |
| `proxmox.node.ksm.shared` | By | `node_name`, `cluster_name` |

### Node — rrddata

| Metric | Description | Attributes |
|---|---|---|
| `proxmox.node.cpu.iowait` | CPU iowait % | `node_name`, `cluster_name` |
| `proxmox.node.memory.available` | Memory available incl. cache | `node_name`, `cluster_name` |
| `proxmox.node.zfs.arcsize` | ZFS ARC size | `node_name`, `cluster_name` |
| `proxmox.node.pressure.cpu.some` | CPU PSI some % | `node_name`, `cluster_name` |
| `proxmox.node.pressure.io.some` | IO PSI some % | `node_name`, `cluster_name` |
| `proxmox.node.pressure.io.full` | IO PSI full % | `node_name`, `cluster_name` |
| `proxmox.node.pressure.memory.some` | Memory PSI some % | `node_name`, `cluster_name` |
| `proxmox.node.pressure.memory.full` | Memory PSI full % | `node_name`, `cluster_name` |

### Node — services / subscription / updates / tasks / netstat

| Metric | Attributes |
|---|---|
| `proxmox.node.service.status` | `node_name`, `cluster_name`, `service_name` |
| `proxmox.node.subscription.active` | `node_name`, `cluster_name` |
| `proxmox.node.updates.pending` | `node_name`, `cluster_name` |
| `proxmox.node.updates.proxmox_pending` | `node_name`, `cluster_name` |
| `proxmox.node.tasks.errors` | `node_name`, `cluster_name` |
| `proxmox.node.tasks.running` | `node_name`, `cluster_name` |
| `proxmox.node.netstat.in` | `node_name`, `cluster_name`, `vmid`, `iface` |
| `proxmox.node.netstat.out` | `node_name`, `cluster_name`, `vmid`, `iface` |

### Physical Disks

| Metric | Description | Attributes |
|---|---|---|
| `proxmox.node.disk.device.size` | Disk size | `node_name`, `cluster_name`, `disk_dev` |
| `proxmox.node.disk.device.smart` | SMART health (1=PASSED,0=FAILED,-1=unknown) | `node_name`, `cluster_name`, `disk_dev` |
| `proxmox.node.disk.device.smart_attr` | SMART raw attribute value | `node_name`, `cluster_name`, `disk_dev`, `smart_attr` |

SMART attributes tracked: `reallocated_sectors`, `power_on_hours`, `uncorrectable_errors`, `command_timeout`, `pending_sectors`, `uncorrectable_sector_count`, `udma_crc_errors`.

### VM — live

| Metric | Attributes |
|---|---|
| `proxmox.vm.status` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.cpu.usage` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.cpu.count` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.memory.used` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.memory.total` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.balloon.current` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.balloon.target` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.disk.read` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.disk.write` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.disk.size` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.network.in` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.network.out` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.uptime` | `vmid`, `vm_name`, `node_name`, `cluster_name` |

### VM — rrddata

| Metric | Attributes |
|---|---|
| `proxmox.vm.disk.used` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.memory.host` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.pressure.cpu.some` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.pressure.cpu.full` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.pressure.io.some` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.pressure.memory.some` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.pressure.memory.full` | `vmid`, `vm_name`, `node_name`, `cluster_name` |

### VM — config

| Metric | Attributes |
|---|---|
| `proxmox.vm.config.cores` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.config.sockets` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.config.memory_mib` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.config.balloon_mib` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.config.cpulimit` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.config.cpuunits` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.config.onboot` | `vmid`, `vm_name`, `node_name`, `cluster_name` |
| `proxmox.vm.snapshot.count` | `vmid`, `vm_name`, `node_name`, `cluster_name` |

### VM — Guest Agent

| Metric | Attributes |
|---|---|
| `proxmox.vm.agent.disk.used` | `vmid`, `vm_name`, `node_name`, `cluster_name`, `mountpoint` |
| `proxmox.vm.agent.disk.total` | `vmid`, `vm_name`, `node_name`, `cluster_name`, `mountpoint` |
| `proxmox.vm.agent.net.rx_bytes` | `vmid`, `vm_name`, `node_name`, `cluster_name`, `iface` |
| `proxmox.vm.agent.net.tx_bytes` | `vmid`, `vm_name`, `node_name`, `cluster_name`, `iface` |
| `proxmox.vm.agent.net.rx_errors` | `vmid`, `vm_name`, `node_name`, `cluster_name`, `iface` |
| `proxmox.vm.agent.net.tx_errors` | `vmid`, `vm_name`, `node_name`, `cluster_name`, `iface` |
| `proxmox.vm.agent.net.rx_dropped` | `vmid`, `vm_name`, `node_name`, `cluster_name`, `iface` |
| `proxmox.vm.agent.net.tx_dropped` | `vmid`, `vm_name`, `node_name`, `cluster_name`, `iface` |

### LXC — live

| Metric | Attributes |
|---|---|
| `proxmox.lxc.status` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.cpu.usage` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.cpu.count` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.memory.used` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.memory.total` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.swap.used` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.swap.total` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.disk.read` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.disk.write` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.disk.size` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.network.in` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.network.out` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |

### LXC — rrddata

| Metric | Attributes |
|---|---|
| `proxmox.lxc.disk.used` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.memory.host` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.pressure.cpu.some` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.pressure.cpu.full` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.pressure.io.some` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.pressure.memory.some` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.pressure.memory.full` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |

### LXC — config

| Metric | Attributes |
|---|---|
| `proxmox.lxc.config.cores` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.config.memory_mib` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.config.swap_mib` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.config.cpulimit` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.config.cpuunits` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.config.onboot` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.config.unprivileged` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |
| `proxmox.lxc.snapshot.count` | `vmid`, `lxc_name`, `node_name`, `cluster_name` |

### Storage

| Metric | Attributes |
|---|---|
| `proxmox.storage.used` | `storage_name`, `node_name`, `cluster_name` |
| `proxmox.storage.total` | `storage_name`, `node_name`, `cluster_name` |
| `proxmox.storage.avail` | `storage_name`, `node_name`, `cluster_name` |
| `proxmox.storage.enabled` | `storage_name`, `node_name`, `cluster_name` |
| `proxmox.storage.active` | `storage_name`, `node_name`, `cluster_name` |
| `proxmox.storage.backup_count` | `storage_name`, `node_name`, `cluster_name` |

### HA / Replication / Backups

Covered in the Cluster section above.

---

## Project Structure

```
proxmox-bindplane-source/
├── collector/
│   ├── __init__.py
│   ├── main.py              # Entry point, config loading, run loop
│   ├── proxmox_client.py    # Proxmox REST API client (proxmoxer)
│   └── metrics.py           # OTel meter setup + all gauges + collection logic
├── config.yaml              # Default configuration
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── bindplane/
│   ├── source-config.yaml   # BindPlane OTLP source config reference
│   └── pipeline-example.yaml
└── README.md
```
