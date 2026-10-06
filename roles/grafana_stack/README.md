# grafana_stack

Optional metrics stack: [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
(Prometheus Operator, Prometheus, Alertmanager, Grafana, node-exporter, kube-state-metrics),
installed as a Flux HelmRelease in the `monitoring` namespace. It is disabled by default and
independent of `elastic_cloud`/`jaeger`, so both can run side by side.

- Grafana is served at `https://grafana.<cluster_fqdn>` and logs in through Keycloak (client
  `grafana`, client roles `admin`/`editor`/`viewer`). The realm admin user gets `grafana: admin`. A
  local `admin` account stays available as break-glass; its password is
  `grafana_stack.grafana.admin_password`.
- Requires `keycloak.enabled`: Grafana logs in through a Keycloak client, and the role stops with
  a message when Keycloak is disabled.
- Prometheus and Alertmanager have no Ingress, because they cannot be protected without Kong EE.
  Use `kubectl -n monitoring port-forward svc/tavros-prometheus 9090` instead.
- Scrapes Kong (prometheus plugin, status port 8100), the Kuma control plane, Kuma data planes
  (MeshMetric + MADS discovery), the Flux controllers and cert-manager, plus the cluster defaults of
  kube-prometheus-stack. Dashboards for these are provisioned in the Grafana folder `Tavros`.
- Monitors live in `platform/grafana-stack-monitors`, a Flux Kustomization that depends on
  `grafana-stack`. This ordering makes sure the prometheus-operator CRDs exist first.

```yaml
grafana_stack:
  enabled: true
  namespace: monitoring    # namespace of every resource of the stack
  keycloak:
    realm: prod
  prometheus:
    retention: 15d
    storage: 20Gi
  alertmanager:
    slack_webhook: ""      # optional
    storage: 2Gi
  grafana:
    storage: 2Gi
  resources:               # per component: prometheus, alertmanager, grafana, loki, alloy
    grafana:
      limits: { memory: 1Gi }
  loki:
    enabled: false         # logs: Loki (single binary) + Grafana Alloy on every node
    retention: 168h
    storage: 10Gi
    error_alert: { threshold: 50, for: 5m }   # TavrosHighErrorLogRate
  tls_skip_verify: false   # true only when platform certs are not publicly trusted
  scrape_control_plane: false  # controller-manager/scheduler/etcd/kube-proxy (localhost-bound on kOps/kind)
```

Every component gets resource requests by default (see `defaults/main.yaml`); Grafana, Loki and
Alloy also get a memory limit. Prometheus has no limit because its memory grows with the number of
series, so size `resources.prometheus` for your cluster. A key you set under `resources.<component>`
is merged with the default, so `resources.prometheus.limits` adds a limit and keeps the requests.

With `loki.enabled`, Loki runs as a single binary with one replica and filesystem storage, and
Grafana Alloy collects the logs of every pod on every node. This fits a single-tenant platform
(ADR-0029) but is not highly available; scale out by moving Loki to object storage. Grafana gets a
Loki data source, a `Tavros Logs` dashboard and the `TavrosHighErrorLogRate` alert.

The dashboards in `files/dashboards/` are vendored from grafana.com (Kong 7424, Kuma
13416/11775/17278, cert-manager 20842) and from fluxcd/flux2-monitoring-example, with the
datasource pinned to the `prometheus` uid.
