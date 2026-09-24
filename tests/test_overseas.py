import unittest
from overseas import dedicated_config


class OverseasTests(unittest.TestCase):
    def test_dedicated_route_ignores_mainland_bypass_without_mutating_source(self):
        source = {'inbounds':[{'port':10808}], 'outbounds':[{'tag':'proxy','protocol':'vless','settings':{'test':'value'}},{'tag':'direct','protocol':'freedom'}], 'routing':{'rules':[{'domain':['geosite:cn'],'outboundTag':'direct'}]}}
        config = dedicated_config(source,12345)
        self.assertEqual(config['routing']['rules'],[{'type':'field','inboundTag':['cinema-overseas'],'outboundTag':'proxy'}])
        self.assertEqual(config['inbounds'][0]['listen'],'127.0.0.1')
        self.assertEqual(config['inbounds'][0]['port'],12345)
        config['outbounds'][0]['settings']['test']='changed'
        self.assertEqual(source['outbounds'][0]['settings']['test'],'value')
        self.assertEqual(source['routing']['rules'][0]['outboundTag'],'direct')

    def test_no_silent_direct_fallback(self):
        with self.assertRaises(RuntimeError):
            dedicated_config({'outbounds':[{'tag':'proxy','protocol':'freedom'}]},12345)
