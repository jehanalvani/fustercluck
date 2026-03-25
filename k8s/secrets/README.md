# Sealed Secrets Management

This directory contains sealed Kubernetes secrets for the cluster. Sealed Secrets allow you to safely commit encrypted secrets to git without exposing sensitive data.

## Prerequisites

Install the `kubeseal` CLI tool on your workstation:

```bash
# Linux
wget https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.18.0/kubeseal-0.18.0-linux-amd64.tar.gz
tar xfz kubeseal-0.18.0-linux-amd64.tar.gz
sudo install -m 755 kubeseal /usr/local/bin/kubeseal

# macOS with Homebrew
brew install kubeseal
```

Ensure your k3s cluster is running and you have `kubectl` configured to access it.

## Getting the Cluster's Public Key

Before sealing any secrets, retrieve your cluster's public sealing key. This key is used to encrypt secrets offline:

```bash
kubeseal --fetch-cert \
  --controller-name=sealed-secrets-controller \
  --controller-namespace=sealed-secrets \
  > pub-cert.pem
```

Store `pub-cert.pem` safely on your workstation. This is a public key (non-sensitive) but only works with your specific cluster.

## Creating a Sealed Secret

Follow these steps to create a sealed secret:

### Step 1: Create a regular Kubernetes secret manifest

Create an unsealed secret with your actual values (never commit this):

```bash
kubectl create secret generic my-secret \
  --namespace=media \
  --from-literal=MY_KEY=myvalue \
  --from-literal=MY_PASS=mypassword \
  --dry-run=client -o yaml > /tmp/my-secret.yaml
```

Inspect the file to ensure it has the correct structure:

```bash
cat /tmp/my-secret.yaml
```

### Step 2: Seal the secret

Encrypt the secret using your cluster's public key:

```bash
kubeseal --format yaml \
  --cert pub-cert.pem \
  < /tmp/my-secret.yaml \
  > k8s/secrets/my-sealed-secret.yml
```

The sealed secret is now safe to commit to git.

### Step 3: Apply it to the cluster

```bash
kubectl apply -f k8s/secrets/my-sealed-secret.yml
```

The sealed-secrets controller automatically decrypts and creates the regular Secret.

### Step 4: Destroy the plaintext

Securely delete the unsealed secret file:

```bash
shred -u /tmp/my-secret.yaml
```

Or on macOS:

```bash
rm -P /tmp/my-secret.yaml
```

## CRITICAL: Back up the Controller Key

The sealed-secrets controller uses a master key to decrypt all sealed secrets. **If you lose this key, you cannot decrypt your sealed secrets.** Back it up immediately and store it securely OFF the cluster.

```bash
kubectl get secret \
  -n sealed-secrets \
  -l sealedsecrets.bitnami.com/sealed-secrets-key \
  -o yaml > sealed-secrets-master-key-BACKUP.yaml
```

Store this file encrypted (e.g., in Ansible Vault, encrypted volume, or password manager). If you're using Ansible for infrastructure management, add it to your Ansible Vault:

```bash
ansible-vault encrypt sealed-secrets-master-key-BACKUP.yaml
```

## Restoring the Master Key

If you need to restore the cluster or migrate secrets to a new cluster:

```bash
# Restore from backup
kubectl apply -f sealed-secrets-master-key-BACKUP.yaml

# Verify the key was loaded
kubectl get secret \
  -n sealed-secrets \
  -l sealedsecrets.bitnami.com/sealed-secrets-key
```

## Namespace Scoping

**Important:** Sealed Secrets are namespace-scoped by default. A secret sealed for the `media` namespace cannot be used in the `monitoring` namespace.

When creating a sealed secret:

```bash
# This seals the secret FOR the 'media' namespace
kubectl create secret generic my-secret \
  --namespace=media \
  ... \
  --dry-run=client -o yaml | kubeseal --cert pub-cert.pem -o yaml
```

If you need to use the same secret in multiple namespaces, you have two options:

1. Create sealed secrets in each namespace with the same values
2. Use a cluster-wide sealed secret (less recommended for security)

## Migrating from Ansible Vault

During node provisioning, sensitive information (SSH keys, system user passwords, etc.) is managed by Ansible Vault and stays there. However, secrets that pods need at runtime should be converted to Sealed Secrets:

- **Ansible Vault:** System-level secrets, infrastructure credentials
- **Sealed Secrets:** Application secrets, API keys, credentials passed to containers

Example workflow:

```bash
# In your Ansible vault file:
nzbget_user: "my-nzbget-user"
nzbget_pass: "secret-password"

# Create sealed secret:
kubectl create secret generic nzbget-secret \
  --namespace=media \
  --from-literal=NZBGET_USER=my-nzbget-user \
  --from-literal=NZBGET_PASS=secret-password \
  --dry-run=client -o yaml | kubeseal --cert pub-cert.pem -o yaml > k8s/secrets/nzbget-sealed-secret.yml

# Then reference it in your pod spec:
containers:
  - name: nzbget
    envFrom:
      - secretRef:
          name: nzbget-secret
```

## Common Operations

### Update an existing sealed secret

```bash
# Recreate the unsealed secret with new values
kubectl create secret generic nzbget-secret \
  --namespace=media \
  --from-literal=NZBGET_USER=new-user \
  --from-literal=NZBGET_PASS=new-password \
  --dry-run=client -o yaml > /tmp/nzbget-secret.yaml

# Seal it
kubeseal --format yaml --cert pub-cert.pem < /tmp/nzbget-secret.yaml > k8s/secrets/nzbget-sealed-secret.yml

# Apply
kubectl apply -f k8s/secrets/nzbget-sealed-secret.yml

# Clean up
shred -u /tmp/nzbget-secret.yaml
```

### View a sealed secret's encrypted content

```bash
kubectl get sealedsecret nzbget-secret -n media -o yaml
```

### View the decrypted secret (only works on cluster)

```bash
kubectl get secret nzbget-secret -n media -o yaml
```

## Troubleshooting

### "sealing key not found"

The sealed-secrets controller hasn't started or isn't running. Check:

```bash
kubectl get pods -n sealed-secrets
```

### "cannot unseal with this key"

You're trying to unseal with the wrong key, or the secret was sealed for a different namespace/cluster. Verify:

1. The namespace matches where the secret is deployed
2. You're using the correct cluster (check kubeconfig context)
3. The sealing key backup is the correct one for this cluster

### "kubeseal command not found"

Install kubeseal (see Prerequisites section above).
