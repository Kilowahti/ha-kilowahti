# Contribution guidelines

Contributing to this project should be as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features

## GitHub is used for everything

GitHub is used to host code, to track issues and feature requests, as well as accept pull requests.

Pull requests are the best way to propose changes to the codebase.

1. Fork the repo and create your branch from `main`.
2. If you've changed something, update the documentation.
3. Make sure your code lints (`ruff check` and `ruff format --check`).
4. Test your contribution against a live Home Assistant instance.
5. Issue that pull request!

## Any contributions you make will be under the MIT Software License

In short, when you submit code changes, your submissions are understood to be under the same [MIT License](http://choosealicense.com/licenses/mit/) that covers the project.

## Report bugs using GitHub's [issues](../../issues)

GitHub issues are used to track public bugs.
Report a bug by [opening a new issue](../../issues/new/choose); it's that easy!

## Write bug reports with detail, background, and sample code

**Great bug reports** tend to have:

- A quick summary and/or background
- Steps to reproduce
  - Be specific!
  - Include relevant configuration (region, price resolution, etc.)
- What you expected to happen
- What actually happens
- Notes (possibly including why you think this might be happening, or things you tried that didn't work)
- HA version and Kilowahti version

## Use a consistent coding style

Use [Ruff](https://docs.astral.sh/ruff/) to make sure the code follows the style:

```bash
ruff check custom_components/kilowahti/
ruff format custom_components/kilowahti/
```

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
