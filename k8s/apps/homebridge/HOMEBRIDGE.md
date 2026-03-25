# Homebridge

HomeKit bridge for smart home integrations, deployed in the `homebridge` namespace.

## Access

- **UI**: https://homebridge.alvani.me

## Architecture

Runs with `hostNetwork: true` — required for mDNS/Bonjour so iOS devices can discover the HomeKit bridge on the local network. Without host networking, HomeKit pairing and device discovery don't work.

`dnsPolicy: ClusterFirstWithHostNet` is set alongside hostNetwork to preserve DNS resolution for cluster services.

## Node Pinning

Pinned to a specific node via `nodeSelector: homekit-bridge: "true"`. This label must be present on whichever node should host Homebridge. The HomeKit pairing is tied to the node's MAC address/network interface — if the pod moves to a different node, HomeKit will lose the bridge and require re-pairing.

To set the label:
```bash
kubectl label node <node-name> homekit-bridge=true
```

## Pairing

Initial HomeKit pairing requires a **physical button press** on the bridge — this cannot be done remotely. The pairing QR code is shown in the Homebridge UI. Access the UI first, then scan from the Home app.

Lutron Caseta integration specifically requires the Lutron Smart Bridge to be on the same network and may require the Caseta physical remote pairing button to be pressed during setup.

## Config Portability

The Homebridge config lives in the PVC at `/homebridge/config.json`. This is **not in the repo**. If the PVC is lost, all plugin configurations and HomeKit pairings are gone.

Pending: extract `config.json` and store as a ConfigMap/template in the repo so it survives redeploys.

```bash
# Extract current config
kubectl exec -n homebridge deploy/homebridge -- cat /homebridge/config.json
```

## Persistence

PVC provisioned by Helm via `nfs-client` StorageClass (2Gi). Unlike most other services, this is **not** a pre-existing claim — Helm manages it.

> If you `helm uninstall` Homebridge, the PVC will be deleted and the config (including HomeKit pairing state) is lost. Use `kubectl delete deployment` instead if you need to redeploy without losing config.
