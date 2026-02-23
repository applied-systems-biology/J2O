# your_app/forms.py
from django import forms
from .models import IDs
import re

class RangeInputForm(forms.ModelForm):
    raw_number_list = forms.CharField(
        label="Enter dataset IDs",
        help_text="e.g., 1,2,3 or 1-3, 5-8",
        required=True
    )

    class Meta:
        model = IDs
        fields = ['raw_number_list']

    def clean_raw_number_list(self):
        data = self.cleaned_data['raw_number_list']
        pattern = r'(\d+-\d+|\d+)'
        matches = re.findall(pattern, data)
        numbers = set()

        for match in matches:
            if '-' in match:
                start, end = map(int, match.split('-'))
                numbers.update(range(start, end + 1))
            else:
                numbers.add(int(match))

        return sorted(numbers)

    def save(self, commit=True):
        instance = super().save(commit=False)
        numbers = self.cleaned_data['raw_number_list']
        print(f"Type: {type(numbers)}, Value: {numbers}")
        # Save the list directly to the JSONField
        instance.id_list_field = numbers
        if commit:
            instance.save()
        return instance
