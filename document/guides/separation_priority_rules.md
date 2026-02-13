# Product Type Separation Priority Rules

## Critical Requirement
The 4 product types must NEVER overlap. Each item belongs to exactly ONE type.

## Separation Priority (Highest to Lowest)

### 1. Hand Carry (HIGHEST PRIORITY)
- **Identification**: UPC exists in PostgreSQL `rps.carrier_item` table
- **Priority**: If an item is hand carry, it is ONLY hand carry
- **Important**: Even if the vendor code is VHN, ROM, TED, etc. (jewelry vendors), the item is treated as hand carry, NOT jewelry
- **Example**: ROM bracelet with UPC 210944 → Hand Carry (1% commission), NOT Jewelry

### 2. Suitcase (TVL, TIT)
- **Identification**: Vendor code is 'TVL' or 'TIT' AND NOT hand carry
- **Commission**: 500,000 VND flat per item
- **Note**: TVL and TIT are NOT jewelry (is_jewelry = 0)

### 3. Jewelry
- **Identification**: Vendor code in ('VHN', 'ROM', 'ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'SPK', 'TED', 'BRT') AND NOT hand carry AND NOT suitcase
- **Commission**: Percentage-based (1-3%) based on vendor and category
- **Note**: Only non-hand-carry items can be jewelry

### 4. Non-Jewelry (LOWEST PRIORITY)
- **Identification**: Everything else
- **Commission**: Achievement-based tiers (0.25-2%)

## Implementation Logic

```python
# Step 1: Identify hand carry items (HIGHEST PRIORITY)
hand_carry_df = all_sales_df[all_sales_df['upc'].isin(hand_carry_upcs)]

# Step 2: Get non-hand-carry items
non_hand_carry_df = all_sales_df[~all_sales_df['upc'].isin(hand_carry_upcs)]

# Step 3: Within non-hand-carry, identify suitcases
suitcase_df = non_hand_carry_df[non_hand_carry_df['vendor_code'].isin(['TVL', 'TIT'])]

# Step 4: Within non-hand-carry, identify jewelry
jewelry_df = non_hand_carry_df[non_hand_carry_df['is_jewelry'] == 1]

# Step 5: Everything else is non-jewelry
non_jewelry_df = non_hand_carry_df[
    (non_hand_carry_df['is_jewelry'] == 0) &
    (~non_hand_carry_df['vendor_code'].isin(['TVL', 'TIT']))
]
```

## Jewelry Commission Rules (Within Jewelry Only)

Once an item is identified as jewelry (not hand carry, not suitcase), apply ONE of these rules:

### 1. ROM EARRINGS (HIGHEST PRIORITY)
- Vendor: 'ROM' AND Category: 'EARRINGS'
- Commission: 3%

### 2. VHN
- Vendor: 'VHN'
- Commission: 1%

### 3. ROM Other
- Vendor: 'ROM' AND Category != 'EARRINGS'
- Commission: 2%

### 4. Fine Jewelry
- Vendor: 'ATS', 'VIS', 'LUI', 'NAN', 'NAK', 'SPK', 'TED', 'BRT'
- Commission: 2%

## Example Cases

### Case 1: TED Bracelet (UPC: 221648)
- UPC 221648 is in hand carry list
- Vendor TED is a jewelry vendor
- **Result**: Hand Carry (1% commission for TED vendor in hand carry)
- **NOT**: Jewelry (2% commission)

### Case 2: ROM Bracelet (UPC: not in hand carry list)
- UPC is NOT in hand carry list
- Vendor ROM is a jewelry vendor
- Category is not EARRINGS
- **Result**: Jewelry - ROM Other (2% commission)

### Case 3: ROM Earrings (UPC: not in hand carry list)
- UPC is NOT in hand carry list
- Vendor ROM is a jewelry vendor
- Category is EARRINGS
- **Result**: Jewelry - ROM EARRINGS (3% commission)

### Case 4: ROM Earrings (UPC: in hand carry list)
- UPC IS in hand carry list
- **Result**: Hand Carry (3% commission for ROM EARRINGS in hand carry)
- **NOT**: Jewelry (3% commission)
- **Note**: Same commission rate but different category!

## Verification Tests

Run `tests/manual/verify_no_overlap_conditions.py` to verify:
1. No item is in more than one product type
2. No jewelry item matches more than one jewelry rule
