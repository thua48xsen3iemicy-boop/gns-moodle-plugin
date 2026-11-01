// This file is part of Moodle - https://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Moodle is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with Moodle.  If not, see <https://www.gnu.org/licenses/>.

/** @module mod_gns3lab/authoringapi */
import Ajax from 'core/ajax';

/** Adapt the existing viewer and console modules to the server-checked library session. */
export const authoringTransport = (labid, definition) => requests => requests.map(request => {
    const operations = {
        mod_gns3lab_get_state: ['state', 'state'],
        mod_gns3lab_start_session: ['start', 'session'],
        mod_gns3lab_stop_session: ['stop', 'session'],
        mod_gns3lab_get_console_ticket: ['ticket', 'ticket'],
    };
    const operation = operations[request.methodname];
    if (!operation) {
        return Promise.reject(new Error('Unsupported authoring operation'));
    }
    const args = {labid, action: operation[0]};
    if (operation[0] === 'start') {
        args.definition = definition;
    }
    if (operation[0] === 'ticket') {
        args.nodeid = request.args.nodeid;
    }
    return Ajax.call([{methodname: 'mod_gns3lab_authoring', args}])[0].then(result => result[operation[1]]);
});
