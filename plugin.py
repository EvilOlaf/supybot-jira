###
# Copyright (c) 2013, Adam Harwell
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

import supybot.utils as utils
import supybot.conf as conf
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
from jira.client import JIRA
import re
from oauthlib.oauth1 import SIGNATURE_RSA
from requests_oauthlib import OAuth1Session
import sqlite3

try:
    from supybot.i18n import PluginInternationalization
    _ = PluginInternationalization('Jira')
except:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    _ = lambda x:x

class Jira(callbacks.PluginRegexp):
    """This plugin communicates with Jira. It will automatically snarf
    Jira ticket numbers, and reply with some basic information
    about the ticket. It can also close and comment on Jira tasks."""
    threaded = True
    unaddressedRegexps = ['getIssue']
    flags = 0

    def __init__(self, irc):
        self.__parent = super(Jira, self)
        self.__parent.__init__(irc)
        self.server = self.registryValue('server')
        self.user = self.registryValue('user')
        self.password = self.registryValue('password')
        self.template = self.registryValue('template')
        self.verifySSL = self.registryValue('verifySSL')
        self.consumer_name = self.registryValue('OAuthConsumerName')
        self.consumer_key = self.registryValue('OAuthConsumerKey')
        self.rsa_key_file = self.registryValue('OAuthConsumerSSLKey')
        self.request_token_url = "%s/plugins/servlet/oauth/request-token" % self.server
        self.access_token_url = "%s/plugins/servlet/oauth/access-token" % self.server
        self.authorize_url = "%s/plugins/servlet/oauth/authorize" % self.server
        self.tokenstore = sqlite3.connect(self.registryValue('OAuthTokenDatabase'))
        try:
            self.tokenstore.execute('''CREATE TABLE tokens (user, request_token, request_token_secret, access_token, access_token_secret)''')
        except:
            pass
        options = { 'server': self.server, 'verify': self.verifySSL }
        auth = (self.user, self.password)
        self.jira = JIRA(options = options, basic_auth = auth)

    def __del__(self):
        self.tokenstore.close()

    def getIssue(self, irc, msg, match):
        """Get a Jira Issue"""
        if not ircutils.isChannel(msg.args[0]):
            return
        issueName = match.group('issue')
        try:
            issue = self.jira.issue(issueName)
        except:
            irc.reply("cannot find %s bug" % issueName, action=True)
            print "Invalid Jira snarf: %s" % issueName
            return

        if issue:
            try:
                assignee = issue.fields.assignee.displayName
            except:
                assignee = "Unassigned"

            try:
                time = issue.fields.timeestimate
                hours = time / 60 / 60
                minutes = time / 60 % 60
                displayTime = " / %ih%im" % (hours, minutes)
            except:
                displayTime = ""

            url = ''.join((self.server, '/browse/', issue.key))

            values = {  "type": issue.fields.issuetype.name,
                        "key": issue.key,
                        "summary": issue.fields.summary,
                        "status": _c(_b(issue.fields.status.name), "green"),
                        "assignee": _c(assignee, "light blue"),
                        "displayTime": displayTime,
                        "url": url,
                    }

            replytext = (self.template % values)
            irc.reply(replytext, prefixNick=False)
    getIssue.__doc__ = '(?P<issue>%s)' % conf.supybot.plugins.Jira.snarfRegex

    def comment(self, irc, msg, args, matched_ticket, comment):
        """<ticket> <comment> takes ticket ID-number and the comment

        Should return nothing, but might if bad things happen."""

        try:
            if self.jira.add_comment(matched_ticket.string, comment):
                irc.reply("OK")
        except:
            irc.reply("cannot comment")
            print "Cannot comment on: %s" % matched_ticket.string
            return
    comment = wrap(comment, [('matches', re.compile(str(conf.supybot.plugins.Jira.snarfRegex)), "The first argument should be the ticket number, but it doesn't match the pattern."), 'text'])

    def ResolveIssue(self, irc, matched_ticket, resolution, comment):
        irc.reply("attempts to close issue %s." % matched_ticket.string, action=True)
        try:
            issue = self.jira.issue(matched_ticket.string)
        except:
            irc.reply("cannot find %s bug" % matched_ticket.string, action=True)
            print "Invalid Jira snarf: %s" % matched_ticket.string
            return

        if issue.fields.status.name == "Resolved":
            irc.reply("Too late! The %s issue is already resolved." % matched_ticket.string)
            return

        try:
            transitions = self.jira.transitions(issue)
        except:
            irc.reply("cannot get transitions states")
            return
        for t in transitions:
            if t['to']['name'] == "Resolved":
                try:
                    self.jira.transition_issue(issue, t['id'], { "resolution": {"name": resolution} }, comment)
                except:
                    irc.reply("Cannot transition to Resolved")
                    return
                irc.reply("Resolved successfully")
                return
        irc.reply("No transition to Resolved state possible from the ticket.")

    def resolve(self, irc, msg, args, matched_ticket, comment):
        """<ticket> <comment> takes ticket ID-number and optionally closing comment

        Should return nothing, but might if bad things happen."""
        self.ResolveIssue(irc, matched_ticket, "Fixed", comment)
    resolve = wrap(resolve, [('matches', re.compile(str(conf.supybot.plugins.Jira.snarfRegex)), "The first argument should be the ticket number, but it doesn't match the pattern."), optional('text')])

    def wontfix(self, irc, msg, args, matched_ticket, comment):
        """<ticket> <comment> takes ticket ID-number and optionally closing comment

        Should return nothing, but might if bad things happen."""
        self.ResolveIssue(irc, matched_ticket, "Won't Fix", comment)
    wontfix = wrap(wontfix, [('matches', re.compile(str(conf.supybot.plugins.Jira.snarfRegex)), "The first argument should be the ticket number, but it doesn't match the pattern."), optional('text')])

    def gettoken(self, irc, msg, args, force):
        """takes no arguments, or 'force' to override old token

        Requests an OAuth token for the bot so that it can act in the name of the user."""
        if (force != None and force != "force"):
            irc.reply("Wrong syntax.")
            return

        #Get user name. Very simple. Assumes that the data in ident is authoritative and no-one can fake it.
        user = msg.user

        try:
            accesstokenlist = tokenstore.execute('SELECT access_token FROM tokens WHERE user=?', (user,))
            token = ''
            for atoken in accesstokenlist:
                token = 
            if (accesstoken != '' and force != "force"):
                irc.reply("You seem to already have a token. Use force to get a new one.")
                return
        except:
            pass

        try:
            f = open(self.rsa_key_file)
            rsa_key = f.read()
        except:
            print "Cannot access the rsa key file %s" % rsa_key_file
            irc.reply("Internal bot error, can't find Jira cert")
            return

        oauth = OAuth1Session(self.consumer_key, signature_type='auth_header', 
                              signature_method=SIGNATURE_RSA, rsa_key=rsa_key)
        request_token = oauth.fetch_request_token(self.request_token_url)

        irc.reply("Please go to %s?oauth_token=%s" % (self.authorize_url, request_token['oauth_token']), private=True, notice=False)
        irc.reply("After that's done, use the bot command 'committoken'", private=True, notice=False)

        usertoken = conf.registerGroup(conf.supybot.plugins.Jira.tokens, user)
        usertoken.register( "request_token",
            registry.String(request_token['oauth_token'], "%s request token" % user, private=True ))
        usertoken.register( "request_token_secret",
            registry.String(request_token['oauth_token_secret'], "%s request token secret" % user, private=True ))

    gettoken = wrap(gettoken, [ optional('text') ])

    def committoken(self, irc, msg, args):
        """takes no arguments.

        Tells the bot that the requested token is fine."""
        irc.reply("Sorry. Not implemented yet.")
    committoken = wrap(committoken)

def _b(text):
    return ircutils.bold(text)

def _c(text, color):
    return ircutils.mircColor(text, color)

Class = Jira

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
