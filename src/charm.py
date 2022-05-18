#!/usr/bin/env python3
# Copyright 2022 Robert Carlsen
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk


import logging
import sys
import time

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
        self.framework.observe(self.on.ccache_stats_action, self._on_ccache_stats_action)
        self._stored.set_default(things=[])

    def _on_icecream_pebble_ready(self, event):
        container = event.workload
        self._install_workload(container)

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
            self.unit.status = wait_service(container, 'icecc-scheduler')

            # set scheduler address for peer worker nodes
            r = self.model.get_relation('nodes')
            scheduler_addr = str(self.model.get_binding(r).network.bind_address)
            r.data[self.app].update({'scheduler_addr': scheduler_addr})

    def _on_peer_relation_changed(self, event):
        self._restart_worker(event)

    def _on_config_changed(self, event):
        self._restart_worker(event)

    def _on_ccache_stats_action(self, event):
        #foo = event.params["foo"]
        state = WorldState(charm=self)
        if not state.connected:
            event.fail(message='not connected to workload')

        process = state.container.exec(['ccache', '--show-stats'])
        stats, stderr = process.wait_output()
        event.set_results({"stats": stats})

    def _on_scheduler_action(self, event):
        #foo = event.params["foo"]
        state = WorldState(charm=self)
        if not state.connected:
            event.fail(message='not connected to workload')

        event.set_results({"address": state.scheduler_addr})

    def _install_workload(self, container):
        # install icecream, ccache on workload container
        try:
            process = container.exec(['apt', 'update', '-y'])
            process.wait()
            process = container.exec(['apt', 'install', '-y', 'icecc', 'ccache'], stdout=sys.stdout, stderr=sys.stderr)
            process.wait()
            process.wait()
        except Exception as err:
            logger.error('Failed to update and install packages: {}'.format(err))
            self.unit.status = BlockedStatus('failed icecc install')
            return

    def _restart_worker(self, event):
        state = WorldState(charm=self)
        if state.need_worker_restart():
            state.container.add_layer("iceccd", state.worker_layer(), combine=True)
            state.container.replan()
            self.unit.status = wait_service(state.container, 'iceccd')
        else:
            logger.debug('got event {!r} with no scheduler address'.format(event))

def wait_service(container, service_name, interval=1, n_try=10):
    for i in range(n_try):
        try:
            s = container.get_service(service_name)
        except Exception as err:
            logger.debug(err)
        if s.is_running():
            return ActiveStatus()
        time.sleep(interval)
    return BlockedStatus('timed out waiting for service {}'.format(service_name))

class WorldState:
    def __init__(self, charm=None):
        self.charm = charm
        self.connected = False # workload
        self.workload_updated = False
        self.scheduler_addr = None
        self.storage_location = None # ccache
        self.workload_path = None

        if charm is None:
            return

        self.container = charm.unit.get_container('icecream')

        c = self.container
        self.connected = c.can_connect()
        if not self.connected:
            return

        process = c.exec(['printenv', 'PATH'])
        envpath, stderr = process.wait_output()
        self.workload_path = envpath.strip()

        r = self.charm.model.get_relation('nodes')
        if 'scheduler_addr' in r.data[self.charm.app]:
            self.scheduler_addr = r.data[self.charm.app]['scheduler_addr']

        if 'ccache' in charm.model.storages:
            self.storage_location = charm.model.storages['ccache'][0].location

    def need_worker_restart(self):
        return self.connected and self.scheduler_addr # and <layer changed?>

    def worker_layer(self):
        env = {}
        if self._do_ccache():
            env['CCACHE_PREFIX'] = 'icecc'
            env['CCACHE_DIR'] = str(self.storage_location)
            env['PATH'] = '/usr/lib/ccache:{}'.format(self.workload_path)

        return {
            "services": {
                "iceccd": {
                    "override": "replace",
                    "summary": "icecream worker service",
                    "command": "iceccd -vvv -s {}".format(self.scheduler_addr),
                    "startup": "enabled",
                    "environment": env,
                }
            },
        }

    def _do_ccache(self):
        return self.storage_location is not None and self.charm.config['ccache']


if __name__ == "__main__":
    main(IcecreamK8SOperatorCharm)
