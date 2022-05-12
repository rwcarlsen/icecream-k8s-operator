#!/usr/bin/env python3
# Copyright 2022 Robert Carlsen
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk


import logging
import sys

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus

logger = logging.getLogger(__name__)


class IcecreamK8SOperatorCharm(CharmBase):
    """Charm the service."""

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.icecream_pebble_ready, self._on_icecream_pebble_ready)
        self.framework.observe(self.on.nodes_relation_changed, self._on_peer_relation_changed)

        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.fortune_action, self._on_fortune_action)
        self._stored.set_default(things=[])

    def _on_icecream_pebble_ready(self, event):
        container = event.workload

        # install icecream on workload container
        try:
            process = container.exec(['apt', 'update', '-y'])
            process.wait()
            process = container.exec(['apt', 'install', '-y', 'icecc'], stdout=sys.stdout, stderr=sys.stderr)
            process.wait()
        except Exception as err:
            logger.error('Failed to install package: {}'.format(err))
            self.unit.status = BlockedStatus('failed icecc install')
            return

        if self.unit.is_leader():
            # start the scheduler
            pebble_layer = {
                "services": {
                    "icecc-scheduler": {
                        "override": "replace",
                        "summary": "icecream scheduler service",
                        "command": "icecc-scheduler -vvv",
                        "startup": "enabled",
                    }
                },
            }
            container.add_layer("icecc-scheduler", pebble_layer, combine=True)

            container.autostart()
            self.unit.status = ActiveStatus()

            # set scheduler address for peer worker nodes
            r = self.model.get_relation('nodes')
            scheduler_addr = str(self.model.get_binding(r).network.bind_address)
            r.data[self.app].update({'scheduler_addr': scheduler_addr})

    def _on_peer_relation_changed(self, event):

        scheduler_addr = None
        if 'scheduler_addr' in event.relation.data[self.app]:
            scheduler_addr = event.relation.data[self.app]['scheduler_addr']

        if scheduler_addr:
            pebble_layer = {
                "services": {
                    "iceccd": {
                        "override": "replace",
                        "summary": "icecream worker service",
                        "command": "iceccd -vvv -s {}".format(scheduler_addr),
                        "startup": "enabled",
                    }
                },
            }
            container = self.unit.get_container('icecream')
            container.add_layer("iceccd", pebble_layer, combine=True)
            container.replan()
            self.unit.status = ActiveStatus()

    def _on_config_changed(self, _):
        current = self.config["thing"]
        if current not in self._stored.things:
            logger.debug("found a new thing: %r", current)
            self._stored.things.append(current)

    def _on_fortune_action(self, event):
        fail = event.params["fail"]
        if fail:
            event.fail(fail)
        else:
            event.set_results({"fortune": "A bug in the code is worth two in the documentation."})


if __name__ == "__main__":
    main(IcecreamK8SOperatorCharm)
