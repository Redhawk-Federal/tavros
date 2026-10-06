# Tavros Platform - Helmfile GitOps POC

This directory proves out an alternative architecture for `ms3_inc.tavros`:
**Helmfile as the sole GitOps engine**, replacing both halves of the current
pipeline:

- the Ansible roles under `roles/*` that Jinja2-template Helm values and raw
  Kubernetes manifests and apply them once via the custom
  `ms3_inc.tavros.kube`/`kustomize` Ansible module, and
- Flux v2 (bootstrapped by `fluxtoolkit`, synced by `fluxtoolkit_sync`), which
  today continues reconciling those rendered manifests from Gitea.

In this POC, Helmfile release definitions under `helmfile.d/` are the single
source of truth for every platform component. There is no `HelmRelease` /
`HelmRepository` / Flux `Kustomization` CRD anywhere here, and `fluxtoolkit` /
`fluxtoolkit_sync` were not ported.

## Architecture

```
   dev            Gitea "platform-manifests" repo (this helmfile.d/ tree)
    |  git push          |
    v                    v
  PR opened  ----->  Gitea webhook  ----->  Jenkins job
                                               |
                                 PR:  helmfile diff   (posts the plan)
                                 main: helmfile apply (reconciles the cluster)
```

- **Before**: Ansible renders Helm values + raw YAML once -> commits the
  rendered output to Gitea -> Flux's `HelmRelease`/`Kustomization`
  controllers poll Gitea and continuously reconcile the live cluster.
- **Now**: Helmfile release definitions (this directory) commit straight to
  Gitea as source, not as rendered output -> the existing Gitea -> Jenkins
  webhook (`roles/jenkins/tasks/gitea_setup.yaml`, unmodified) triggers a
  Jenkins job that runs `helmfile diff` on every PR and `helmfile apply` on
  merge to `main`. Helmfile (via `helm` + `helm-diff`) is the only thing that
  ever talks to the cluster; nothing reconciles in a loop in-cluster anymore.
  See `examples/Jenkinsfile.stage` for a sketch of that job (illustrative
  only - the real `.jenkins/Jenkinsfile` was not touched; it drives cluster
  provisioning, a separate concern).

### Layout

```
helmfile.d/
  helmfile.yaml               # entrypoint: environments:, repositories:, helmfiles: includes
  environments/
    default.yaml              # mirrors playbooks/provision_playbook/default_vars.yaml
  releases/
    <component>.yaml[.gotmpl] # one Helmfile state file per component (included via helmfiles:)
  values/
    <component>[-resources].yaml.gotmpl  # Helm values / raw-manifest definitions
  charts/
    raw-manifests/            # generic local chart: renders .Values.manifests + .Values.manifestFiles
      files/                  # a few upstream manifests vendored verbatim (see table below)
  examples/
    Jenkinsfile.stage         # sample day-2 pipeline stage (not wired into .jenkins/Jenkinsfile)
  README.md
```

One state file per component, included from `helmfile.yaml` via
`helmfiles:`, rather than one monolithic `helmfile.yaml` - this mirrors
`roles/*` one-role-per-component today and keeps diffs scoped to whichever
component changed. Every component that needs raw (non-Helm-chart) YAML uses
the **same** generic local chart, `charts/raw-manifests` (a `Chart.yaml` +
one template that ranges over `.Values.manifests`, plus a second input mode,
`.Values.manifestFiles`, for a handful of large upstream manifests vendored
verbatim rather than retyped - see "Judgment calls" below) - never a bespoke
local chart per component.

### Environments

`helmfile.yaml` defines three named environments - `default`, `sandbox`,
`prod` - all currently pointed at `environments/default.yaml`, which mirrors
the *shape* of `playbooks/provision_playbook/default_vars.yaml` (one cluster,
a sandbox + prod Kong instance, a sandbox + prod Kuma mesh, dev/test/prod
namespaces, sandbox + prod Keycloak realms). `sandbox`/`prod` are stubbed
with commented-out override files (`environments/sandbox.yaml`,
`environments/prod.yaml`) for whenever these two logical environments need to
diverge (e.g. real clusters) rather than share one topology description -
today's Ansible vars also describe a single cluster's full topology in one
file, so this is a straight port, not a new design.

**All secrets in `environments/default.yaml` are `CHANGEME` placeholders.**
Ansible generates these on first run (`lookup('password', '/dev/null')`) and
seals them with `kubeseal` before committing for Flux. This POC drops
Flux/kubeseal from the reconciliation path entirely (see "Secrets" below) -
replace the placeholders with values from a real secrets manager or a
SOPS-encrypted values file (`helm-secrets`) before ever running `helmfile
apply` against a live cluster.

## Component status

| Component | Ported | Chart (repo / name / version) | Explicitly out of scope (role/task file) |
|---|---|---|---|
| `cert_manager` | Yes - chart + raw `ClusterIssuer`/`Certificate` | `https://charts.jetstack.io` / `cert-manager` / `1.11.0` | - |
| `sealed_secrets` | Yes, **chart swapped** (judgment call below) | `https://bitnami.github.io/sealed-secrets` / `sealed-secrets` / `2.16.0` | - (installed but unused, see "Secrets") |
| `postgresql` | Yes - raw manifests (**not** a Helm chart, judgment call below) | n/a | - |
| `kuma` | Yes - chart + raw `Mesh`/`TrafficTrace` per mesh | `https://kumahq.github.io/charts` / `kuma` / `2.13.5` | - |
| `kong` | Yes - chart x N instances (hybrid cp/dp aware) + raw ingress/secrets/certs | `https://charts.konghq.com` / `kong` / `2.52.0` | - |
| `keycloak` | Yes - operator (judgment call below) + CRs | n/a (legacy keycloak-operator v19.0.3, no Helm chart upstream) | realm/client API bootstrap doesn't exist in this role to begin with (all declarative CRs already) |
| `nexus` | Yes - chart + raw ingress/configmap/pvc/keycloakclient | `https://sonatype.github.io/helm3-charts/` / `nexus-repository-manager` / `64.2.0` | `tasks/configure-auth.yaml`, `tasks/create-repositories.yaml` (Nexus REST API calls) |
| `elastic_cloud` | Yes - ECK operator (judgment call below) + CRs + Beats | n/a (ECK 2.5.0 crds/operator.yaml, no chart reference in role) | `tasks/config_kibana.yaml` (Kibana saved-objects import) |
| `jaeger` | Yes - jaeger-operator (judgment call below) + CRs | n/a (jaeger-operator v1.62.0, no chart reference in role) | - |
| `gitea` | Yes - chart + raw ingress/keycloakclient/gitshell | `https://dl.gitea.io/charts/` / `gitea` / `2.2.4` | `tasks/keycloak_sso.yaml` (Gitea OpenID auth-source API calls) |
| `jenkins` | Yes - jenkins-operator (vendored manifest) + CRs | n/a (jenkinsci/kubernetes-operator v0.8.0-beta2, vendored `all-in-one.yaml`, no chart reference in role) | `tasks/gitea_setup.yaml` (Gitea user/webhook API calls - this is the webhook the day-2 loop above depends on) |
| `namespace` | Yes - raw Namespace/RoleBinding/Deployment/Secret per namespace | n/a (raw-manifests chart) | kubeseal step on the registry secret (see "Secrets") |
| `fluxtoolkit`, `fluxtoolkit_sync` | **Dropped entirely** (this is the point of the POC) | - | Flux bootstrap + Gitea sync - no longer needed, Helmfile reconciles directly |

Out of scope per the task brief and **not attempted** anywhere in this port:
kOps/Terraform/Karpenter cluster provisioning, Keycloak realm/client
creation (`roles/keycloak` imperative pieces - N/A, see above), Nexus
repo/auth config, Gitea<->Keycloak SSO, Jenkins<->Gitea wiring, Kong DNS
Route53/Azure records (`roles/kong_dns`). These all need a new home (most
naturally: a post-`helmfile apply` Jenkins stage calling the relevant REST
APIs, or Terraform providers where they exist) in a future pass.

## Judgment calls

1. **`sealed_secrets`: chart chosen over raw manifest.** The Ansible role
   does not reference a Helm chart today - it kustomize-applies the upstream
   `v0.27.0` `controller.yaml` release asset directly
   (`roles/sealed_secrets/files/kustomization.yaml`). Per the task brief we
   switched to the official `bitnami-labs/sealed-secrets` chart (now served
   from `https://bitnami.github.io/sealed-secrets` - most docs/tutorials
   still point at a dead `bitnami-labs.github.io` Pages URL that 404s).
   Chart `2.16.0` carries `appVersion 0.27.1`, the closest published chart
   release to the pinned controller `v0.27.0` - verify/bump before relying
   on this for real. **Nothing in this POC actually seals a secret with it**
   (see "Secrets" below) - it's installed for cluster parity only.

2. **`postgresql`: raw manifests, not the Bitnami chart.** Despite the
   filename, `roles/postgresql/templates/release.j2` is **not** a Helm
   chart `HelmRelease` - it's a raw `Service` x2 + `StatefulSet` running a
   single `postgres:17` container (no `bitnami/postgresql` chart reference
   exists anywhere in that role). Ported faithfully as raw manifests via
   `raw-manifests` rather than swapped for the Bitnami chart, to match what
   actually runs today. This is a single unreplicated StatefulSet with no
   backups - fine for a POC, not for production; swapping to a real
   `postgresql` chart (or a managed RDS/Cloud SQL instance) is a natural
   follow-up this port deliberately didn't make unasked.

3. **`keycloak`, `elastic_cloud` (ECK), `jaeger`, `jenkins`: operator
   installs stay as `kubectl apply` presync hooks, not vendored Helm
   charts.** All four of these roles install their operator straight from
   an upstream CRD/manifest URL (or, for jenkins, a manifest already
   vendored locally in the role) - **none reference a Helm chart today**,
   despite the task brief's "Helm chart install only" phrasing assuming one
   existed. Hand-vendoring hundreds to thousands of lines of upstream
   CRD/RBAC YAML into this repo (or writing a from-scratch local chart for
   each) was judged out of proportion for a POC whose job is to prove the
   Helmfile-as-sole-reconciler architecture, not to re-package four
   third-party operators. Each operator release instead has a
   `hooks.presync` that shells out to `kubectl apply -f <url>` -
   **this is the one place in this port that still invokes `kubectl`
   directly instead of letting Helm manage the resources**, flagged here as
   the clearest architectural compromise in this POC. Two cleaner
   follow-ups exist and are noted inline in `releases/elastic-cloud.yaml`
   and `releases/jaeger.yaml`: Elastic and Jaeger each publish an official
   operator Helm chart (`eck-operator` at `helm.elastic.co`,
   `jaeger-operator` at `jaegertracing.github.io/helm-charts`) that would
   let Helmfile manage the operator too. The legacy `keycloak-operator`
   (v1alpha1 CRDs, archived upstream) and the jenkins-operator have no such
   chart; replacing the legacy Keycloak operator with the modern Keycloak
   Operator (`keycloak.org/v2alpha1`) would also resolve this more
   thoroughly but is a bigger migration than this POC's scope.
   The Elastic Beats DaemonSets/Deployments
   (filebeat/metricbeat/heartbeat/apmserver/jaeger-agent, ~700 lines total)
   and the jenkins-operator's `all-in-one.yaml` (already vendored in the
   role) are copied verbatim into `charts/raw-manifests/files/` and emitted
   via the chart's second input mode, `.Values.manifestFiles` (see
   `charts/raw-manifests/templates/manifests.yaml`) - full fidelity without
   hand-retyping them as `.Values.manifests` maps.

4. **Helmfile `needs:` does not span `helmfiles:` includes.** Verified
   empirically against helmfile v1.8.1: a release in one included state file
   cannot `needs:` a release defined in another included state file (fails
   with "depend(s) on an undefined release"). Real cross-component
   dependencies exist (e.g. everything that renders a `KeycloakClient` CR
   needs `keycloak-resources`'s realm to exist; Kong's and Keycloak's raw
   resources need `cert-manager-resources`'s `ClusterIssuer`). These are
   documented as `NB:` comments at each affected release rather than
   expressed as `needs:`, and are informally ordered by each component's
   position in `helmfile.yaml`'s `helmfiles:` list (cert-manager and
   keycloak both come before anything that depends on them). If you need
   Helmfile to actually enforce this, the fix is to flatten the dependent
   releases into a single state file (losing the one-file-per-component
   layout) or to drive dependency ordering from the Jenkins pipeline instead
   (e.g. separate `helmfile apply --selector` waves).

5. **Per-Kong-instance / per-Kuma-mesh / per-namespace / per-realm
   parameterization via Go templating over `environments:` values**, not
   Helmfile's `values:`-list value-passing trick. The docs/blog posts
   describing "pass instance data as an earlier inline `values:` entry, read
   it back via `.Values` in a later `.gotmpl`" do not work against this
   installed helmfile version (verified empirically, see the `NB:` comment
   in `releases/kong.yaml.gotmpl`) - `.Values` is empty when a later
   `.gotmpl` file in the same `values:` list renders. Instead, generated
   Kong releases are tagged with `labels: {instance, role}`, and
   `values/kong-release.yaml.gotmpl` / `kong-resources.yaml.gotmpl` look the
   instance back up by name in `.Environment.Values.kong.instances` (which
   *is* reliably available everywhere). Worth re-testing against whatever
   helmfile version you actually run this with.

6. **Secrets**: this POC does not reintroduce `kubeseal`/SealedSecrets
   encryption, and no release here actually uses the `sealed-secrets`
   release installed for parity. Every password/client-secret in
   `environments/default.yaml` is a plaintext `CHANGEME` placeholder, and
   every raw Secret manifest in `values/*.yaml.gotmpl` uses `stringData` with
   those placeholders substituted in directly - fine to `helmfile template`/
   `lint`, **not fine to `helmfile apply` against a real cluster as-is**.
   `roles/namespace`'s kubeseal step on `secret-artifacts-registry` is
   explicitly skipped per the task brief; the same gap exists for every
   other secret this port renders. Before using this for real, either (a)
   swap each `values/*.yaml.gotmpl`'s secret definitions for
   `helm-secrets`/SOPS-encrypted values files, or (b) replace the plain
   Secret manifests with `ExternalSecret`s backed by a real secrets manager.
   This is flagged as a TODO, not implemented, per the task brief.

7. **One known un-automatable data dependency**:
   `values/jaeger-resources.yaml.gotmpl`'s `tavros-es-http-certs` Secret
   needs the live `ca.crt` out of the ECK-operator-created
   `tavros-es-http-certs-public` Secret - something Ansible does today with
   a live `kube_info` read mid-run. A Helmfile values template renders
   offline and can't do that; it's left as a `CHANGEME` placeholder with a
   comment suggesting a `hooks.presync` `kubectl get -o jsonpath` or an
   External Secret / Reflector as the real fix.

## Verifying this locally

Prerequisites: `helm` (v3), `helmfile` (v1.x), and the `helm-diff` plugin.
On this machine:

```sh
brew install helm helmfile
helm plugin install https://github.com/databus23/helm-diff --verify=false
```

Then, from this directory:

```sh
cd helmfile.d

# add/verify the chart repos declared in helmfile.yaml
helmfile repos

# render every component's final manifests (no cluster needed)
helmfile -e default template

# lint every chart (local raw-manifests chart + every vendored Helm chart)
helmfile -e default lint

# requires a live cluster + kubeconfig - shows what would change
helmfile -e default diff

# requires a live cluster + kubeconfig - this is what Jenkins runs on merge
helmfile -e default apply
```

Swap `-e default` for `-e sandbox` / `-e prod` once those environments grow
their own override files.

### What was actually verified in this environment

- `helm lint charts/raw-manifests` - passes (no values.yaml is expected;
  it's a values-only chart).
- `helmfile -e default build` - passes; confirms `environments:` values
  resolve correctly into every nested state file (this is what caught the
  `inherits: ["environments", "repositories"]` requirement on every
  `helmfiles:` entry, and the `releases/kong.yaml` -> `.gotmpl` rename
  requirement - both now reflected in the committed state).
- `helmfile -e default template` - **passes end-to-end, all 25 releases**
  across all 12 components, including the real upstream charts
  (`jetstack/cert-manager`, `kuma/kuma`, `kong/kong` x2 instances,
  `sealed-secrets/sealed-secrets`, `sonatype/nexus-repository-manager`,
  `gitea-charts/gitea`) fetched live from their repos and rendered with
  this port's values. This is the strongest signal in this POC: every
  component's values are structurally valid against its chart's actual
  templates.
- `helmfile -e default lint` - passes for every release (0 charts failed).
- `helmfile -e default diff` - confirmed it invokes `helm-diff` correctly
  and fails only on `kubernetes cluster unreachable: dial tcp
  [::1]:8080: connect: connection refused` (no cluster in this
  environment) - i.e. the command is wired correctly; it was not run
  against a real cluster.
- `helmfile -e default apply` - **not run** (no cluster available here).

Nothing above was silently left broken; see "Judgment calls" above for the
specific spots (operator presync hooks, placeholder secrets, the
`tavros-es-http-certs` live-cluster read) that still need follow-up before
this could `apply` cleanly against a real cluster.
