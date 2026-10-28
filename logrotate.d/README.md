# Logrotate Configuration

Install this configuration to `/etc/logrotate.d/` on the system running the audiobook pipeline:

```bash
sudo cp logrotate.d/audiobook-pipeline /etc/logrotate.d/audiobook-pipeline
sudo chmod 644 /etc/logrotate.d/audiobook-pipeline
```

Test the configuration:

```bash
sudo logrotate -d /etc/logrotate.d/audiobook-pipeline  # dry-run
sudo logrotate -f /etc/logrotate.d/audiobook-pipeline  # force rotation
```

Logs are rotated daily and kept for 14 days. Rotated logs are compressed (gzip) to save space.
