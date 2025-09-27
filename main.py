import datetime
from playwright.sync_api import sync_playwright
from dataclasses import dataclass, asdict, field
import pandas as pd
import argparse
import os
import sys
import re
from typing import List, Dict

@dataclass
class Business:
    """holds business data"""
    name: str = None
    address: str = None
    domain: str = None
    website: str = None
    phone_number: str = None
    whatsapp_link: str = None
    category: str = None
    location: str = None
    reviews_count: int = None
    reviews_average: float = None
    latitude: float = None
    longitude: float = None
    
    def __hash__(self):
        """Make Business hashable for duplicate detection.
        Consider businesses different if:
        - Name is different, OR
        - Same name but different non-empty contact info (domain/website/phone)
        """
        hash_fields = [self.name]
        if self.domain:
            hash_fields.append(f"domain:{self.domain}")
        if self.website:
            hash_fields.append(f"website:{self.website}")
        if self.phone_number:
            hash_fields.append(f"phone:{self.phone_number}")
        
        return hash(tuple(hash_fields))

@dataclass
class BusinessList:
    """Holds list of Business objects and saves to both Excel and CSV.

    The output directory is based on `save_base_dir` and the current date.
    For example: <save_base_dir>/<YYYY-MM-DD>/...
    """
    business_list: list[Business] = field(default_factory=list)
    _seen_businesses: set = field(default_factory=set, init=False)
    save_base_dir: str = 'GMaps Data'
    today: str = field(default_factory=lambda: datetime.datetime.now().strftime("%Y-%m-%d"))
    save_at: str = field(init=False)

    def __post_init__(self):
        self.save_at = os.path.join(self.save_base_dir, self.today)
        os.makedirs(self.save_at, exist_ok=True)

    def add_business(self, business: Business):
        """Add a business to the list if it's not a duplicate based on key attributes"""
        business_hash = hash(business)
        if business_hash not in self._seen_businesses:
            self.business_list.append(business)
            self._seen_businesses.add(business_hash)
    
    def dataframe(self, add_status_column: bool = False):
        """transform business_list to pandas dataframe

        Args:
            add_status_column: Se True, adiciona coluna 'status' com valor 1

        Returns: pandas dataframe
        """
        df = pd.json_normalize(
            (asdict(business) for business in self.business_list), sep="_"
        )
        
        # Adicionar coluna status se solicitado
        if add_status_column:
            df['status'] = 1
        
        return df

    def save_to_excel(self, filename):
        """saves pandas dataframe to excel (xlsx) file

        Args:
            filename (str): filename
        """
        try:
            df = self.dataframe()
            out_path = f"{self.save_at}/{filename}.xlsx"
            # Write with openpyxl engine so we can post-process hyperlinks
            with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
                df.to_excel(writer, index=False)
                try:
                    from openpyxl.utils import get_column_letter
                    ws = writer.book.active
                    if "whatsapp_link" in df.columns:
                        col_idx = df.columns.get_loc("whatsapp_link") + 1  # 1-based
                        col_letter = get_column_letter(col_idx)
                        for row_idx in range(2, len(df) + 1):  # skip header
                            cell = ws[f"{col_letter}{row_idx}"]
                            link = cell.value
                            if link:
                                cell.hyperlink = link
                                cell.style = "Hyperlink"
                except Exception:
                    # If anything goes wrong, keep the plain values without hyperlinks
                    pass
        except ImportError:
            print("openpyxl not installed; skipping Excel export and continuing with CSV...")
        except Exception as e:
            print(f"Failed to write Excel: {e}; continuing with CSV...")

    def save_to_csv(self, filename):
        """saves pandas dataframe to csv file

        Args:
            filename (str): filename
        """
        self.dataframe().to_csv(f"{self.save_at}/{filename}.csv", index=False)

    def save_to_excel_with_status(self, filename):
        """
        Salva dataframe com coluna status adicionada

        Args:
            filename (str): filename
        """
        try:
            df = self.dataframe(add_status_column=True)
            out_path = f"{self.save_at}/{filename}.xlsx"
            # Write with openpyxl engine so we can post-process hyperlinks
            with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
                df.to_excel(writer, index=False)
                try:
                    from openpyxl.utils import get_column_letter
                    ws = writer.book.active
                    if "whatsapp_link" in df.columns:
                        col_idx = df.columns.get_loc("whatsapp_link") + 1  # 1-based
                        col_letter = get_column_letter(col_idx)
                        for row_idx in range(2, len(df) + 1):  # skip header
                            cell = ws[f"{col_letter}{row_idx}"]
                            link = cell.value
                            if link:
                                cell.hyperlink = link
                                cell.style = "Hyperlink"
                except Exception:
                    # If anything goes wrong, keep the plain values without hyperlinks
                    pass
        except ImportError:
            print("openpyxl not installed; skipping Excel export and continuing with CSV...")
        except Exception as e:
            print(f"Failed to write Excel: {e}; continuing with CSV...")

    def save_to_csv_with_status(self, filename):
        """saves pandas dataframe to csv file with status column

        Args:
            filename (str): filename
        """
        self.dataframe(add_status_column=True).to_csv(f"{self.save_at}/{filename}.csv", index=False)

def concatenate_business_lists(business_lists: List[BusinessList]) -> BusinessList:
    """
    Concatena múltiplas BusinessList em uma única, com deduplicação automática
    """
    if not business_lists:
        return BusinessList()
    
    # Usar a primeira BusinessList como base
    result = business_lists[0]
    
    # Adicionar businesses das outras listas
    for business_list in business_lists[1:]:
        for business in business_list.business_list:
            result.add_business(business)  # Deduplicação automática
    
    return result

def extract_coordinates_from_url(url: str) -> tuple[float, float]:
    """helper function to extract coordinates from url"""
    coordinates = url.split('/@')[-1].split('/')[0]
    return float(coordinates.split(',')[0]), float(coordinates.split(',')[1])


def format_whatsapp_link_br(raw_phone: str) -> str:
    """Return a WhatsApp wa.me link for Brazilian numbers.
    - Keeps only digits
    - Ensures country code 55 is present
    - Returns empty string if no digits are found
    """
    if not raw_phone:
        return ""
    digits_only = re.sub(r"\D", "", raw_phone)
    if not digits_only:
        return ""
    if not digits_only.startswith("55"):
        digits_only = "55" + digits_only
    return f"https://wa.me/{digits_only}"


def run_scraper(
    search_list: List[str],
    total: int,
    headless: bool = True,
    save_base_dir: str | None = None,
    concatenate_results: bool = False,  # Nova opção
) -> List[Dict[str, str]]:
    """Run scraping for one or more searches.

    Args:
        search_list: Lista de queries para buscar
        total: Número máximo de resultados por busca
        headless: Executar browser em modo headless
        save_base_dir: Diretório base para salvar arquivos
        concatenate_results: Se True, concatena todos os resultados em um arquivo único

    Returns a list of dicts with keys: search, csv_path, xlsx_path
    """
    results: List[Dict[str, str]] = []
    all_business_lists: List[BusinessList] = []
    with sync_playwright() as p:
        # Browser launch with fallbacks
        try:
            browser = p.chromium.launch(headless=headless)
        except Exception:
            try:
                browser = p.chromium.launch(channel="chrome", headless=headless)
            except Exception:
                browser = p.chromium.launch(channel="msedge", headless=headless)
        page = browser.new_page(locale="en-GB")

        page.goto("https://www.google.com/maps", timeout=20000)

        for search_for_index, search_for in enumerate(search_list):
            print(f"-----\n{search_for_index + 1}/{len(search_list)} - {search_for}".strip())

            page.locator('//input[@id="searchboxinput"]').fill(search_for)
            page.wait_for_timeout(3000)

            page.keyboard.press("Enter")
            page.wait_for_timeout(5000)

            # scrolling
            page.hover('//a[contains(@href, "https://www.google.com/maps/place")]')

            previously_counted = 0
            while True:
                page.mouse.wheel(0, 10000)
                page.wait_for_timeout(3000)

                if (
                    page.locator(
                        '//a[contains(@href, "https://www.google.com/maps/place")]'
                    ).count()
                    >= total
                ):
                    listings = page.locator(
                        '//a[contains(@href, "https://www.google.com/maps/place")]'
                    ).all()[:total]
                    listings = [listing.locator("xpath=..") for listing in listings]
                    print(f"Total Scraped: {len(listings)}")
                    break
                else:
                    if (
                        page.locator(
                            '//a[contains(@href, "https://www.google.com/maps/place")]'
                        ).count()
                        == previously_counted
                    ):
                        listings = page.locator(
                            '//a[contains(@href, "https://www.google.com/maps/place")]'
                        ).all()
                        print(f"Arrived at all available\nTotal Scraped: {len(listings)}")
                        break
                    else:
                        previously_counted = page.locator(
                            '//a[contains(@href, "https://www.google.com/maps/place")]'
                        ).count()
                        print(
                            f"Currently Scraped: ",
                            page.locator(
                                '//a[contains(@href, "https://www.google.com/maps/place")]'
                            ).count(), end='\r'
                        )

            business_list = BusinessList(
                save_base_dir=save_base_dir or 'GMaps Data'
            )

            # scraping
            for listing in listings:
                try:
                    listing.click()
                    page.wait_for_timeout(2000)

                    name_attribute = 'h1.DUwDvf'
                    address_xpath = '//button[@data-item-id="address"]//div[contains(@class, "fontBodyMedium")]'
                    website_xpath = '//a[@data-item-id="authority"]//div[contains(@class, "fontBodyMedium")]'
                    phone_number_xpath = '//button[contains(@data-item-id, "phone:tel:")]//div[contains(@class, "fontBodyMedium")]'
                    review_count_xpath = '//div[@jsaction="pane.reviewChart.moreReviews"]//span'
                    reviews_average_xpath = '//div[@jsaction="pane.reviewChart.moreReviews"]//div[@role="img"]'

                    business = Business()

                    if name_value := page.locator(name_attribute).inner_text():
                        business.name = name_value.strip()
                    else:
                        business.name = ""

                    if page.locator(address_xpath).count() > 0:
                        business.address = page.locator(address_xpath).all()[0].inner_text()
                    else:
                        business.address = ""

                    if page.locator(website_xpath).count() > 0:
                        business.domain = page.locator(website_xpath).all()[0].inner_text()
                        business.website = f"https://www.{page.locator(website_xpath).all()[0].inner_text()}"
                    else:
                        business.website = ""

                    if page.locator(phone_number_xpath).count() > 0:
                        raw_phone = page.locator(phone_number_xpath).all()[0].inner_text()
                        business.phone_number = raw_phone
                        business.whatsapp_link = format_whatsapp_link_br(raw_phone)
                    else:
                        business.phone_number = ""
                        business.whatsapp_link = ""

                    if page.locator(review_count_xpath).count() > 0:
                        business.reviews_count = int(page.locator(review_count_xpath).inner_text().split()[0].replace(',', '').strip())
                    else:
                        business.reviews_count = ""

                    if page.locator(reviews_average_xpath).count() > 0:
                        business.reviews_average = float(page.locator(reviews_average_xpath).get_attribute('aria-label').split()[0].replace(',', '.').strip())
                    else:
                        business.reviews_average = ""

                    business.category = search_for.split(' in ')[0].strip()
                    business.location = search_for.split(' in ')[-1].strip()
                    business.latitude, business.longitude = extract_coordinates_from_url(page.url)

                    business_list.add_business(business)
                except Exception as e:
                    print(f'Error occurred: {e}', end='\r')

            # Armazenar business_list para concatenação posterior
            all_business_lists.append(business_list)
            
            # Se não for para concatenar, salvar individualmente
            if not concatenate_results:
                safe_filename = f"{search_for}".replace(' ', '_')
                business_list.save_to_excel(safe_filename)
                business_list.save_to_csv(safe_filename)
                results.append({
                    "search": search_for,
                    "csv_path": os.path.join(business_list.save_at, f"{safe_filename}.csv"),
                    "xlsx_path": os.path.join(business_list.save_at, f"{safe_filename}.xlsx"),
                })

        browser.close()
    
    # Se for para concatenar, criar arquivo único
    if concatenate_results and all_business_lists:
        concatenated = concatenate_business_lists(all_business_lists)
        
        # Nome do arquivo baseado na primeira busca
        first_search = search_list[0] if search_list else "multiple_locations"
        base_keyword = first_search.split(' in ')[0].strip()
        safe_filename = f"{base_keyword}_múltiplos_bairros"
        
        # Salvar com coluna status adicionada
        concatenated.save_to_excel_with_status(safe_filename)
        concatenated.save_to_csv_with_status(safe_filename)
        
        results.append({
            "search": f"{base_keyword} em {len(search_list)} localizações",
            "csv_path": os.path.join(concatenated.save_at, f"{safe_filename}.csv"),
            "xlsx_path": os.path.join(concatenated.save_at, f"{safe_filename}.xlsx"),
        })
    
    return results


def main():
    # read search from arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--search", type=str)
    parser.add_argument("-t", "--total", type=int)
    args = parser.parse_args()
    
    if args.search:
        search_list = [args.search]
    
    if args.total:
        total = args.total
    else:
        total = 1_000_000

    if not args.search:
        search_list = []
        input_file_name = 'input.txt'
        input_file_path = os.path.join(os.getcwd(), input_file_name)
        if os.path.exists(input_file_path):
            with open(input_file_path, 'r') as file:
                search_list = file.readlines()
                
        if len(search_list) == 0:
            print('Error occured: You must either pass the -s search argument, or add searches to input.txt')
            sys.exit()
    
    # CLI uses headful browser for visibility
    run_scraper(search_list=search_list, total=total, headless=False)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f'Failed err: {e}')
