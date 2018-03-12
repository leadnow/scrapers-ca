# coding: utf-8
from utils import CanadianScraper, CanadianPerson as Person

import re

COUNCIL_PAGE = 'https://www.ville.quebec.qc.ca/apropos/gouvernance/conseil-municipal/membres.aspx'


class QuebecPersonScraper(CanadianScraper):
    def scrape(self):
        page = self.lxmlize(COUNCIL_PAGE)

        sections = page.xpath('//div[contains(@class, "membres-conseil-municipal")]')
        for section in sections:
            councillors = section.xpath('./div')
            assert len(councillors), 'No councillors found'
            for councillor in councillors:
                name = councillor.xpath('./h3')[0].text_content()
                if 'vacant' in name:
                    continue

                header = section.xpath('./preceding-sibling::h2/text()')[-1]
                if 'Mairie' in header:
                    district = 'Québec'
                    role = 'Maire'
                else:
                    text = councillor.xpath('./p[@itemprop="jobTitle"]/a/text()')[0]
                    district = re.search(r'\ADistrict (?:de|du|des) ([\w —–-]+)', text, flags=re.U).group(1)
                    role = 'Conseiller'

                if district == 'Monts':
                    district = 'Les Monts'
                elif district == 'Plateau':
                    district = 'Le Plateau'
                else:
                    district = re.sub(r'–', '—', district)  # n-dash, m-dash
                    district = re.sub(r'\Ala ', 'La ', district)

                p = Person(primary_org='legislature', name=name, district=district, role=role)
                p.add_source(COUNCIL_PAGE)
                p.image = councillor.xpath('./figure//@src')[0]
                p.add_contact('voice', self.get_phone(councillor, area_codes=[418]), 'legislature')
                yield p
