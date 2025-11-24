You are working on implementing new cloud provider for Verda Cloud (formerly DataCrunch), in skypilot. 
The goal is to make a branch and a pull request to the official Skypilot github repo which is up to standard.

Skypilot official docs https://docs.skypilot.co/ 
DataCrunch python api: https://github.com/DataCrunch-io/datacrunch-python

Consider guide here: ./adding-cloud-provider.md
And also take a look at existing merged recent provider: https://github.com/skypilot-org/skypilot/pull/6884

The catalog lists (might be incorrect) one VM to be available, see  /Users/ruslan/.sky/catalogs/v8/verda/vms.csv

Possible tasks

* Cloud provider should be configurable properly (works), `sky check` command works.
* When you launch it should show GPU VMs available from Verda Cloud
* `sky show-gpus` should work - throws exception
* Creating instance in sky/provision/verda code should be completely rewritten and use our python api at https://github.com/DataCrunch-io/datacrunch-python


