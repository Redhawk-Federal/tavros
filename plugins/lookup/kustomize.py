# https://docs.ansible.com/ansible/latest/dev_guide/developing_plugins.html#lookup-plugins
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = """
        lookup: kustomize
        author: Jose Montoya <jmontoya@ms3-inc.com>
        version_added: "0.1.0"
        short_description: build kustomization
        description:
            - This lookup returns the results of `kustomize build` on a given directory on the Ansible controller's file system.
        options:
          _terms:
            description: path(s) of directories to run kustomize on
            required: True
          reorder:
            description: Reorder the resources just before output. Use 'legacy' to apply a legacy reordering (Namespaces first, Webhooks last, etc). Use 'none' to suppress a final reordering. (default 'legacy')
            required: False
"""
from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase
from ansible.utils.display import Display

import os
import tempfile
from subprocess import CalledProcessError, PIPE, run
import yaml

display = Display()

class LookupModule(LookupBase):

    def run(self, terms, variables=None, **kwargs):
        ret = []
        self.reorder = kwargs.get('reorder')

        for term in terms:
            display.debug("Kustomize lookup term: %s" % term)
            # kustomize v5 removed --enable_kyaml, --allow_id_changes and --reorder,
            # and renamed --load_restrictor to --load-restrictor.
            args = ['kustomize', 'build', '--load-restrictor=LoadRestrictionsNone']

            with tempfile.TemporaryDirectory() as wrapper:
                target = term
                # 'none' used to mean "keep input order"; in kustomize v5 that is
                # sortOptions.order=fifo, which is only honoured on the top-level
                # kustomization, so wrap the term in one.
                if self.reorder == 'none':
                    with open(os.path.join(wrapper, 'kustomization.yaml'), 'w') as f:
                        yaml.safe_dump({
                            'apiVersion': 'kustomize.config.k8s.io/v1beta1',
                            'kind': 'Kustomization',
                            'sortOptions': {'order': 'fifo'},
                            # kustomize rejects absolute resource paths
                            'resources': [os.path.relpath(os.path.abspath(term), wrapper)],
                        }, f)
                    target = wrapper

                args.append(target)

                try:
                    result = run(args, check=True, stderr=PIPE, stdout=PIPE)
                    resources = yaml.safe_load_all(result.stdout)
                    ret.extend(r for r in resources if r is not None)

                except CalledProcessError as err:
                    raise AnsibleError('error running kustomize (%s) command: %s' % (' '.join(args), err.stderr))

        return ret
