<!-- Thank you for your contribution! -->

## What do these changes do?

<!-- Please give a short brief about these changes. -->

## Are there changes in behavior for the user?

<!-- Outline any notable behaviour for the end users. -->

## Related issue number

<!-- Are there any issues opened that will be resolved by merging this change? -->
<!-- Use the "closing keywords" such as "Closes" or "Fixes" to automatically link to Issues. -->

## Checklist

- [ ] I think the code is well written
- [ ] Unit tests for the changes exist
- [ ] tox testenvs have been executed in the following environments:
  <!-- These are just examples; add/remove as necessary -->
  - [ ] Linux (Ubuntu 18.04, Ubuntu 20.04, Arch): `{py36,py37,py38,py39}-{nocov,cov,diffcov}, qa, docs`
  - [ ] Windows (7, 10): `{py36,py37,py38,py39}-{nocov,cov,diffcov}`
  - [ ] WSL 1.0 (Ubuntu 18.04): `{py36,py37,py38,py39}-{nocov,cov,diffcov}, pypy3-{nocov,cov}, qa, docs`
  - [ ] FreeBSD (12.2, 12.1, 11.4): `{py36,pypy3}-{nocov,cov,diffcov}, qa`
  - [ ] Cygwin: `py36-{nocov,cov,diffcov}, qa, docs`
- [ ] Documentation reflects the changes
- [ ] Add a news fragment into the `NEWS.rst` file
  <!-- Delete the following bullet points prior to submitting the PR -->
  * Add under the "aiosmtpd-next" section, creating one if necessary
    * You may create subsections to group the changes, if you like
  * Use full sentences with correct case and punctuation
  * Refer to relevant Issue if applicable
