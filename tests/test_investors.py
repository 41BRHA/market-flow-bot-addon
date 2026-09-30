import tempfile
import unittest

from smart_money_tracker.app.investors import InvestorStore, MANAGERS, parse_information_table


XML = b'''<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
<infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass>
<cusip>037833100</cusip><value>1000000</value><shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt>
<sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><investmentDiscretion>DFND</investmentDiscretion>
<votingAuthority><Sole>5000</Sole><Shared>0</Shared><None>0</None></votingAuthority></infoTable></informationTable>'''


class InvestorTests(unittest.TestCase):
    def setUp(self):
        self.store = InvestorStore(tempfile.NamedTemporaryFile(suffix='.db').name)
        self.manager = MANAGERS[0]
        self.row = parse_information_table(XML, '2026-08-14')[0]

    def filing(self, accession, filed, period, form='13F-HR'):
        return {'accession':accession, 'cik':self.manager['cik'], 'manager':self.manager['name'],
                'filed_date':filed, 'report_period':period, 'form':form,
                'source_url':'https://www.sec.gov/example'}

    def test_current_xml_value_is_dollars_and_ticker_is_not_guessed(self):
        self.assertEqual(self.row['value'], 1_000_000)
        self.assertEqual(self.row['shares'], 5_000)
        self.assertEqual(self.row['ticker'], '')

    def test_pre_2023_xml_value_was_thousands(self):
        self.assertEqual(parse_information_table(XML, '2022-12-01')[0]['value'], 1_000_000_000)

    def test_share_count_controls_change_direction(self):
        self.store.save_filing(self.filing('a','2026-05-15','2026-03-31'), [{**self.row,'shares':4000,'value':1_200_000}])
        self.store.save_filing(self.filing('b','2026-08-14','2026-06-30'), [self.row])
        change=self.store.comparison(self.manager['cik'])['holdings'][0]
        self.assertEqual(change['change'],'Increased')
        self.assertEqual(change['share_change'],1000)
        self.assertEqual(change['value_change'],-200000) # price/value can fall while shares rise

    def test_amendment_compares_with_prior_quarter(self):
        self.store.save_filing(self.filing('a','2026-05-15','2026-03-31'), [{**self.row,'shares':4000}])
        self.store.save_filing(self.filing('b','2026-08-14','2026-06-30'), [self.row])
        self.store.save_filing(self.filing('c','2026-08-20','2026-06-30','13F-HR/A'), [{**self.row,'shares':6000}])
        data=self.store.comparison(self.manager['cik'])
        self.assertEqual(data['previous']['report_period'],'2026-03-31')
        self.assertEqual(data['holdings'][0]['share_change'],2000)

    def test_enabling_alerts_baselines_existing_filings(self):
        self.store.save_filing(self.filing('a','2026-05-15','2026-03-31'), [self.row])
        saved=self.store.set_subscription(self.manager['cik'],True,['New'],100000)
        self.assertTrue(saved['enabled'])
        self.assertTrue(self.store.delivered('a'))


if __name__ == '__main__': unittest.main()
