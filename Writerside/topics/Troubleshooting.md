# Troubleshooting

This page covers common issues that can arise during installation or usage of J2O.

> If you can't find your issue here, feel free to contact us or open an issue in the [community](https://image.sc).

## Error starting JIPipe task

This error can occur when you have entered all parameters and press the "Start J2O" button. Multiple issues can be the cause for this depending on the exact error message.

### Could not start job. No Celery workers are currently running.

This issue arises when there is no Celery worker attached to the plugin. This issue must be resolved by your system administrator and may be caused by a faulty installation.

#### Solution

As described in the [manual installation guide](ManualInstallationGuide.md), you can start a Celery worker  in your omero-web environment like this:

```bash
celery -A JIPipePlugin worker --loglevel=info -E --detach
```

To terminate workers associated with the plugin, simply run this command:
```bash
celery -A JIPipePlugin control shutdown
```
